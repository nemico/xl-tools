-- 再生・停止（Space の代わり）。止まっていれば、編集カーソルの位置で音源がなっているはずの状態を JSFX から送り、
-- 音源が全部受け取って落ち着いてから（送り終わり ＋ 落ち着く時間）、その位置から再生する。
-- 違う音を鳴らさないため、送り終わる前には再生しない。停止中に JSFX が動いていなければ、再生せずに理由を出す。
-- 前に戻って再生し直す（プリロール）ことも、一時アイテムを作ることもしない。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local proj = dofile(DIR .. "xl_project.lua")
local live = dofile(DIR .. "xl_live.lua")
local chase = dofile(DIR .. "xl_chase.lua")
local sync = dofile(DIR .. "xl_sync.lua")
local log = dofile(DIR .. "xl_log.lua")

local M = {}

M.proj = proj  -- 読んだ MIDI を覚えている部品（XLaben_Sync が、読み直した数を見る）
M.EXT = "XLabenMixer"
M.DEFAULT_SETTLE_MS = 400     -- 前の FastPreroll の「末尾位置」（400ms 前に送り終える）と同じ
M.ALIVE_CHECK_S = 0.25        -- この間に JSFX の印が増えなければ、停止中に FX が動いていない

local busy = nil   -- 待っている最中なら、その印（もう一度押されたら取り消す）
local restarted = false  -- この回に、前の版の XLaben_Sync を起動し直した

local function title() return "X-Laben 再生" end

-- 送り終わってから再生までに待つ時間（ミリ秒）。ミキサーの右クリックで変えられる
function M.settle_ms()
    local v = tonumber(r.GetExtState(M.EXT, "settle_ms"))
    if not v then v = tonumber(r.GetExtState("FastPreroll", "end_offset_ms")) end
    return math.max(0, math.min(2000, v or M.DEFAULT_SETTLE_MS))
end

function M.set_settle_ms(v)
    r.SetExtState(M.EXT, "settle_ms", tostring(math.floor(v)), true)
end

function M.busy() return busy ~= nil end

-- 前から再生する長さ（ミリ秒、0 = 使わない）。使うときは、押したらすぐ「カーソル − この長さ」から再生を始め、
-- 送る・落ち着くのを再生と重ねる。カーソルの位置までは、マスターの A2M Preroll gate が音を出さず、
-- X-Laben の JSFX が前のノートを鳴らさない。短すぎるとき（送る＋落ち着く＋M.PREROLL_MARGIN_MS より短い）は、その長さにする
M.PREROLL_MARGIN_MS = 50
function M.preroll_ms() return tonumber(r.GetExtState(M.EXT, "preroll_ms")) or 0 end
function M.set_preroll_ms(v) r.SetExtState(M.EXT, "preroll_ms", tostring(math.floor(v)), true) end

-- 音源に入っている状態の鍵：JSFX が始まった印 | ふつうの CC 以外が入った数 | プロジェクト | 行き先の sig の印。
-- 前の全部送りのときの鍵と同じなら、音源の「ふつうの CC 以外」は行き先と同じ
function M.sync_key(sig_hash)
    return string.format("%s|%s|%s|%s", tostring(live.session()), tostring(live.structure_count()),
                         tostring(r.EnumProjects(-1)), sig_hash)
end

-- 試験：違う CC だけ送るか（既定はオフ = 毎回全部）
function M.diff_mode() return r.GetExtState(M.EXT, "diff_mode") == "1" end
function M.set_diff_mode(on) r.SetExtState(M.EXT, "diff_mode", on and "1" or "0", true) end

-- 違う CC だけにできない理由（できるなら nil）。鍵は「JSFX の印|ふつうの CC 以外の数|プロジェクト|sig の印」
function M.why_not_synced(synced, key, has_reset)
    if not has_reset then return "プロジェクトに音源全体のリセットが無い" end
    if synced == "" then return "前に全部を送った記録が無い（初めての再生など）" end
    if synced == key then return nil end
    local a, b = {}, {}
    for v in (synced .. "|"):gmatch("([^|]*)|") do a[#a + 1] = v end
    for v in (key .. "|"):gmatch("([^|]*)|") do b[#b + 1] = v end
    if a[1] ~= b[1] then return "JSFX が読み直された" end
    if a[3] ~= b[3] then return "プロジェクトが違う" end
    if a[2] ~= b[2] then
        return string.format("前に全部を送ってから、音源に音色・SysEx・RPN/NRPN などが %d 件入った",
                             (tonumber(b[2]) or 0) - (tonumber(a[2]) or 0))
    end
    return "再生を始める位置で、音色・SysEx・RPN/NRPN などが前と違う"
end

-- 次の再生では、必ず全部を送り直す（ミキサーの右クリックから）
function M.force_full()
    r.SetExtState(M.EXT, "force_full", "1", false)
end

local function not_running_message()
    r.ShowMessageBox(
        "停止中に、X-Laben トラックの JSFX（A2M Live MIDI preview）が動いていません。\n" ..
        "このままだと、音源の設定を変える前に再生が始まり、違う音が鳴るので、再生しませんでした。\n\n" ..
        "REAPER の設定で、停止中も FX を動かすようにしてください：\n" ..
        "  オプション → 設定 → オーディオ → 「停止中も FX を動かす（Run FX when stopped）」\n" ..
        "（または、X-Laben トラックの FX が無効・オフラインになっていないか確かめてください）",
        title(), 0)
end

-- 位置 t で音源がなっているはずの状態にするために、送るもの。
-- {sched = {{msg, 待つミリ秒}, ...}, settle = 送り終わってから待つミリ秒, how, note, plan}
function M.prepare(tracks, t, prefer_diff)
    local full = proj.chase_schedule(tracks, t)
    local plan = chase.plan(full)
    local key = M.sync_key(chase.hash(plan.sig))
    -- 既定は毎回全部を送る（動きを安定させるため）。
    -- 試験：ミキサーの右クリックで「違う CC だけ送る」をオンにすると（または prefer_diff のとき）、前に全部を送ってから
    -- 音源にふつうの CC 以外が何も入っていなくて、行き先でもそこが同じなら、違う CC だけ送る。できなかったときは理由を出す
    local sched, settle, how, note = full, M.settle_ms(), "全部", ""
    if prefer_diff or M.diff_mode() then
        local why = M.why_not_synced(r.GetExtState(M.EXT, "synced"), key, plan.has_reset)
        if not why then
            local channels = {}
            for ch = 1, 16 do if tracks.ch[ch] then channels[#channels + 1] = ch end end
            local msgs = chase.diff(plan, channels, live.cc_value, live.param_value, live.selection)
            if msgs then
                sched, settle, how = {}, 0, "違う CC だけ"
                for i, m in ipairs(msgs) do sched[i] = {m, 0} end
            else
                why = "既定の値が分からない CC・RPN/NRPN を戻す必要がある"
            end
        end
        if why then note = "　違う CC だけにできなかった理由：" .. why end
    end
    if r.GetExtState(M.EXT, "force_full") == "1" then
        sched, settle, how = full, M.settle_ms(), "全部"
        r.DeleteExtState(M.EXT, "force_full", false)
    end
    return {sched = sched, settle = settle, how = how, note = note, plan = plan, all = full}
end

-- 送り終わったときに呼ぶ：音源に入った状態を覚える（このあと、ふつうの CC 以外が入らなければ、次は違う CC だけで済む）
function M.record_sent(plan)
    if plan.has_reset then
        r.SetExtState(M.EXT, "synced", M.sync_key(chase.hash(plan.sig)), false)
    else
        r.DeleteExtState(M.EXT, "synced", false)
    end
end

-- 再生を始める。done(結果の表) を呼ぶ
function M.start(script_root, done)
    local tracks = proj.find_tracks()
    if not tracks.xlaben then
        r.ShowMessageBox("「X-Laben」という名前のトラックがありません。", title(), 0)
        return
    end
    local ok, err = proj.ensure_jsfx(tracks.xlaben, script_root)
    if not ok then
        r.ShowMessageBox(err, title(), 0)
        return
    end
    local t = r.GetCursorPosition()
    local t0 = r.time_precise()
    sync.hold_off(t0)  -- 自分で送る間は、XLaben_Sync は送らない
    local p = M.prepare(tracks, t)
    local sched, settle, how, note, plan = p.sched, p.settle, p.how, p.note, p.plan
    local read_ms = (r.time_precise() - t0) * 1000
    local send_ms = 0
    for _, e in ipairs(sched) do send_ms = send_ms + e[2] end
    log.write("Space", "自分で送る：%s %d 件 t=%.3f 読む %.0f ms 送る %.0f ms 待つ %d ms 前から再生 %d ms%s",
              how, #sched, t, read_ms, send_ms, settle, M.preroll_ms(), note)
    -- 前から再生する（送る・落ち着くのを再生と重ねる）
    local pre = M.preroll_ms()
    if pre > 0 then
        local lead = math.max(pre, send_ms + settle + M.PREROLL_MARGIN_MS) / 1000
        if t - lead >= 0 and #sched < live.SLOTS then
            local ok_gate, gate_err = proj.ensure_gate(script_root)
            if not ok_gate then
                r.ShowMessageBox(gate_err, title(), 0)
                return
            end
            r.DeleteExtState(M.EXT, "synced", false)  -- 試験の「違う CC だけ」は、この再生のあとは使わない
            r.gmem_write(90, t)
            live.send_all(sched)
            r.SetEditCurPos(t - lead, false, false)
            log.write("Space", "前から再生：%.0f ms 前から再生を始め、送るのは再生と重ねる", lead * 1000)
            r.OnPlayButton()
            r.SetEditCurPos(t, false, false)
            r.SetExtState(M.EXT, "last_play", string.format(
                "%s送って、%.0f ms 前から再生：%d 件（読む %.0f ms、送る %.0f ms、落ち着くまで %d ms）%s",
                how, lead * 1000, #sched, read_ms, send_ms, settle, note), false)
            if done then done({count = #sched, read_ms = read_ms, send_ms = send_ms, lead = lead}) end
            return
        end
    end
    local me = {}
    busy = me
    local beat0 = live.heartbeat()
    local rest = live.send_all(sched)
    local started = r.time_precise()
    local sent_at = nil
    local function wait()
        if busy ~= me then return end  -- 取り消された
        local now = r.time_precise()
        sync.hold_off(now)
        if live.heartbeat() == beat0 and now - started > M.ALIVE_CHECK_S then
            busy = nil
            r.gmem_write(1, r.gmem_read(0))  -- 送れなかった分は捨てる
            not_running_message()
            return
        end
        if rest then rest = live.send_all(sched, rest) end
        if not rest and live.pending() <= 0 then
            if not sent_at then
                sent_at = now
                M.record_sent(plan)
            end
            if (now - sent_at) * 1000 >= settle then
                busy = nil
                log.write("Space", "送り終わって %d ms たったので再生（押してから %.0f ms）", settle, (now - t0) * 1000)
                r.OnPlayButton()
                local info = string.format("%s%s送って再生：%d 件（読む %.0f ms、送る %.0f ms、待つ %d ms）%s",
                                           restarted and "前の版の XLaben_Sync を起動し直したので、今回は自分で" or "",
                                           how, #sched, read_ms, (sent_at - started) * 1000, settle, note)
                restarted = false
                r.SetExtState(M.EXT, "last_play", info, false)
                if done then done({count = #sched, read_ms = read_ms, send_ms = send_ms}) end
                return
            end
        end
        r.defer(wait)
    end
    wait()
end

M.SYNC_WAIT_MAX_S = 5   -- XLaben_Sync の準備をこれ以上待つときは、自分で送る

-- XLaben_Sync が先に送ってあれば、すぐ（準備中なら、準備ができてから）再生する。そうできたら true
local function play_synced(script_root, done)
    local t0 = r.time_precise()
    local info = sync.published()
    local fk = sync.fast_key()
    log.write("Space", "押した：Sync 動いている=%s 版=%s 印の古さ=%.2f s エラー=%s 準備の鍵が同じ=%s 準備まで=%.0f ms",
              tostring(info.running), r.GetExtState(M.EXT, "sync_version"), t0 - info.alive, info.error,
              tostring(info.fp ~= "" and info.fp == fk), (info.ready_at - t0) * 1000)
    log.write("Space", "  出ている鍵 %s", info.fp)
    log.write("Space", "  今の鍵     %s", fk)
    if not sync.alive(info, t0) then
        restarted = sync.launch(script_root) == "restarted"  -- 次からは、先に送っておく
        log.write("Space", "Sync が動いていない → 起動（起動し直し=%s）して、今回は自分で送る", tostring(restarted))
        return false
    end
    if info.error ~= "" then
        log.write("Space", "Sync のエラー %s → 自分で送る", info.error)
        return false  -- 理由は、ふつうの再生で出す
    end
    local function ready(now)
        local i = sync.published()
        return i.fp ~= "" and i.fp == sync.fast_key() and now >= i.ready_at, i
    end
    local ok_now, i = ready(t0)
    if ok_now then
        log.write("Space", "準備できていたので、すぐ再生")
        r.OnPlayButton()
        r.SetExtState(M.EXT, "last_play", "先に送ってあったので、すぐ再生（" .. i.how .. "）", false)
        if done then done({count = 0, waited_ms = 0, synced = true}) end
        return true
    end
    -- 準備中：自分では送らない（XLaben_Sync と二重に送ると、再生を始めたあとにリセットが入りうる）。
    -- 急いでもらって（止めた直後の待ちを飛ばす）、送り終わって落ち着くのを待ってから再生する
    sync.set_urgent(true)
    log.write("Space", "準備中なので、Sync に急いでもらって待つ")
    local me = {}
    busy = me
    local function wait()
        if busy ~= me then return end  -- 取り消された
        local now = r.time_precise()
        local ok2, i2 = ready(now)
        if ok2 then
            busy = nil
            sync.set_urgent(false)
            log.write("Space", "準備ができたので再生（%.0f ms 待った）", (now - t0) * 1000)
            r.OnPlayButton()
            r.SetExtState(M.EXT, "last_play", string.format("先に送っている途中だったので、準備ができるまで %.0f ms 待って再生（%s）",
                                                            (now - t0) * 1000, i2.how), false)
            if done then done({count = 0, waited_ms = (now - t0) * 1000, synced = true}) end
            return
        end
        if not sync.alive(i2, now) or i2.error ~= "" or now - t0 > M.SYNC_WAIT_MAX_S then
            log.write("Space", "待つのをやめて自分で送る：動いている=%s エラー=%s 待った %.0f ms",
                      tostring(sync.alive(i2, now)), i2.error, (now - t0) * 1000)
            log.write("Space", "  出ている鍵 %s", i2.fp)
            log.write("Space", "  今の鍵     %s", sync.fast_key())
            busy = nil
            sync.set_urgent(false)
            M.start(script_root, done)
            return
        end
        r.defer(wait)
    end
    r.defer(wait)
    return true
end

-- 再生中なら止める。待っている最中なら取り消す。止まっていれば、先に送ってあればすぐ再生、なければ start
function M.toggle(script_root, done)
    if busy then
        busy = nil
        sync.set_urgent(false)
        return
    end
    local st = r.GetPlayState()
    if st & 5 ~= 0 then  -- 再生中か録音中
        log.write("Space", "止める")
        r.OnStopButton()
        return
    end
    if st ~= 0 then log.write("Space", "一時停止中（%d）から → 自分で送る", st) end
    if not sync.enabled() then log.write("Space", "先に送っておくは切ってある → 自分で送る") end
    if st == 0 and sync.enabled() and play_synced(script_root, done) then return end
    M.start(script_root, done)
end

return M
