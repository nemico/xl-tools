-- 先に送っておく（XLaben_Sync.lua が回す）。止まっている間、次のどれかが変わったら、編集カーソルの位置で音源が
-- なっているはずの状態を送り直し（違うのがふつうの CC だけなら、それだけ。リセットしないので落ち着く時間も要らない）、落ち着く時間も先に待つ：
--   ・編集カーソル（シーク）  ・プロジェクトの中身（MIDI の編集、元に戻す、ミキサー・EFXUI の書き込みなど）
--   ・音源に入ったもの（ミキサーや EFXUI が送った、再生してトラックの MIDI が入った、JSFX が読み直された）
-- 再生中は何もしない。止めた直後（M.TAIL_S の間）にリセットから全部を送るときだけ、鳴り残っている音（リリース・リバーブ）に
-- 効かないように待つ（リセットで鳴り残りの音が急に大きく・エフェクト無しになる）。その間に Space が押されたら、すぐ送る。
-- 違う CC だけなら、止めたらすぐ送る。
-- 音色・SysEx・RPN/NRPN などが違う（その位置をまたいだ、再生中に入った）ときだけ全部（リセットから。落ち着く時間を待つ）。
-- 準備ができたら（送り終わって、全部なら落ち着く時間も過ぎたら）、xl_sync.publish で Space に知らせる。
-- Space が自分で送っている間（XLaben_Sync が起動した直後など）は送らない（二重に送らない）。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local proj = dofile(DIR .. "xl_project.lua")
local live = dofile(DIR .. "xl_live.lua")
local chase = dofile(DIR .. "xl_chase.lua")
local play = dofile(DIR .. "xl_play.lua")
local sync = dofile(DIR .. "xl_sync.lua")
local log = dofile(DIR .. "xl_log.lua")

local M = {}

-- 変わったのを見つけたら、待たずにすぐ送る（カーソルをドラッグしている間に、前の送りが終わる前に動いたら、残りを捨ててやり直す）
M.ALIVE_CHECK_S = 0.25   -- 送り始めてこの間に JSFX の印が増えなければ、停止中に FX が動いていない
M.TAIL_S = 1.0           -- 止めてからこの間は、リセットから全部を送るときだけ、Space が待っていなければ送らない（鳴り残りの音のため）

function M.new(script_root) return {root = script_root} end

-- 送るもの全体（並びと間）の印。同じなら、音源がなっているはずの状態は同じ
local function sched_hash(sched)
    local parts = {}
    for i, e in ipairs(sched) do parts[i] = string.format("%d:", e[2]) .. e[1] end
    return chase.hash(table.concat(parts, "\0"))
end

local function forget(w)
    w.sending, w.done_tk, w.done_ik, w.sent_hash, w.changed, w.held = nil, nil, nil, nil, nil, nil
end

-- 1回分。続けるなら true
function M.tick(w, now)
    sync.beat(now)
    if not sync.enabled() then return false end
    if r.GetPlayState() ~= 0 then  -- 再生・一時停止・録音：音源の中は分からなくなる
        if w.sending or w.done_tk or w.done_ik or w.held then
            forget(w)
            sync.publish(nil)
        end
        if not w.was_playing then log.write("Sync", "再生が始まった（送らない）") end
        w.was_playing = true
        return true
    end
    if w.was_playing then
        w.was_playing, w.stopped_at = false, now
        log.write("Sync", "止まった")
    end
    if sync.held_off(now) then  -- Space が自分で送っている
        if w.sending or w.done_tk or w.held then forget(w); sync.publish(nil) end
        return true
    end
    local urgent = sync.urgent()
    local in_tail = w.stopped_at ~= nil and now - w.stopped_at < M.TAIL_S
    local tk = sync.target_key()
    if r.GetExtState(play.EXT, "force_full") == "1" and not w.sending then w.sent_hash = nil; w.done_tk = nil end

    local s = w.sending
    if s then
        if tk ~= s.tk or live.session() ~= s.session or r.gmem_read(0) ~= s.w0 then
            -- 送っている途中で変わった：自分の残りだけなら捨てて、やり直す
            log.write("Sync", "送っている途中で変わった（位置・編集=%s JSFX=%s 誰かが送った=%s）→ やり直す",
                      tostring(tk ~= s.tk), tostring(live.session() ~= s.session), tostring(r.gmem_read(0) ~= s.w0))
            if r.gmem_read(0) == s.w0 then r.gmem_write(1, r.gmem_read(0)) end
            forget(w)
        else
            if s.rest then
                s.rest = live.send_all(s.sched, s.rest)
                s.w0 = r.gmem_read(0)
            end
            if live.heartbeat() ~= s.beat0 then s.beat_ok = true end
            if not s.beat_ok and now - s.started > M.ALIVE_CHECK_S then sync.set_error("jsfx_not_running") end
            if not s.rest and live.pending() <= 0 then
                sync.set_error("")
                play.record_sent(s.plan)
                w.done_tk, w.done_ik, w.sent_hash = tk, sync.inst_key(), s.hash
                w.last_sig = s.plan.sig_list
                w.ready_at, w.how = now + s.settle / 1000, s.how
                sync.publish(tk .. "#" .. w.done_ik, w.ready_at, s.how)
                w.sending = nil
                w.sends = (w.sends or 0) + 1
                log.write("Sync", "送り終わった：%s %d 件、準備まで %d ms（送るのに %.0f ms）",
                          s.how, #s.sched, s.settle, (now - s.started) * 1000)
            end
            return true
        end
    end

    local ik = sync.inst_key()
    if tk == w.done_tk and ik == w.done_ik then return true end
    local key = tk .. "#" .. ik
    if w.changed ~= key then
        w.changed, w.changed_at, w.held = key, now, nil
    end
    if w.held then
        -- 用意してある。止めた直後なら、Space が待つまで（か、鳴り残りの時間が過ぎるまで）送らない
        if in_tail and not urgent and w.held.full then return true end
        local h = w.held
        w.held = nil
        return M.send(w, h, now)
    end

    local tracks = proj.find_tracks()
    if not tracks.xlaben then
        sync.set_error("no_xlaben")
        log.write("Sync", "X-Laben トラックが無い")
        w.done_tk, w.done_ik = tk, ik
        return true
    end
    local ok = proj.ensure_jsfx(tracks.xlaben, w.root)
    if not ok then
        sync.set_error("jsfx_install")
        log.write("Sync", "JSFX を入れられない")
        w.done_tk, w.done_ik = tk, ik
        return true
    end
    local t_read = r.time_precise()
    local reads0 = play.proj.reads
    local p = play.prepare(tracks, r.GetCursorPosition(), true)  -- できれば違う CC だけ（リセットしない）
    local read_ms = (r.time_precise() - t_read) * 1000
    local h = sched_hash(p.all)
    if h == w.sent_hash and ik == w.done_ik then
        -- 編集はあったが、カーソルの位置でなっているはずの状態は同じ（ノートの編集、選ぶだけ、など）
        log.write("Sync", "変わったが、カーソルの位置の状態は同じ → 送らない")
        w.done_tk = tk
        sync.publish(tk .. "#" .. ik, w.ready_at, w.how)
        return true
    end
    w.held = {tk = tk, sched = p.sched, settle = p.settle, how = p.how, plan = p.plan, hash = h, full = p.sched == p.all}
    log.write("Sync", "用意：t=%.3f %s %d 件（読む %.0f ms、読み直したテイク %d）%s%s", r.GetCursorPosition(), p.how, #p.sched,
              read_ms, play.proj.reads - reads0, p.note,
              (in_tail and not urgent and w.held.full) and "、止めた直後でリセットが要るので Space を待つ（最大 1 秒）" or "")
    log.write("Sync", "  音源の鍵 %s → %s", tostring(w.done_ik), ik)
    if w.held.full and w.last_sig then M.log_sig_diff(w.last_sig, p.plan.sig_list) end
    if in_tail and not urgent and w.held.full then return true end
    local held = w.held
    w.held = nil
    return M.send(w, held, now)
end

local function hex(m)
    return (m:gsub(".", function(c) return string.format("%02X ", c:byte()) end)):sub(1, -2)
end

-- 前に送った「ふつうの CC 以外」と、今の位置のものの違い（前にだけある − / 今だけある +）を、8 件まで記録に出す
function M.log_sig_diff(old, new)
    local count = {}
    for _, m in ipairs(old) do count[m] = (count[m] or 0) + 1 end
    local plus = {}
    for _, m in ipairs(new) do
        if (count[m] or 0) > 0 then count[m] = count[m] - 1 else plus[#plus + 1] = m end
    end
    local minus = {}
    for _, m in ipairs(old) do
        if (count[m] or 0) > 0 then minus[#minus + 1] = m; count[m] = count[m] - 1 end
    end
    log.write("Sync", "  ふつうの CC 以外の違い：前だけ %d 件、今だけ %d 件", #minus, #plus)
    for i = 1, math.min(8, #minus) do log.write("Sync", "    − %s", hex(minus[i])) end
    for i = 1, math.min(8, #plus) do log.write("Sync", "    ＋ %s", hex(plus[i])) end
end

function M.send(w, s, now)
    log.write("Sync", "送る：%s %d 件%s（見つけてから %.0f ms）", s.how, #s.sched, sync.urgent() and "（Space が待っている）" or "",
              (now - (w.changed_at or now)) * 1000)
    sync.set_urgent(false)
    s.session, s.started, s.beat0 = live.session(), now, live.heartbeat()
    s.rest = live.send_all(s.sched)
    s.w0 = r.gmem_read(0)
    w.sending, w.done_tk = s, nil
    return true
end

-- defer で回し続ける（止めたら印を消す）
function M.run(script_root, on_exit)
    local w = M.new(script_root)
    sync.set_running(true)
    log.write("Sync", "起動した（版 %d）", sync.VERSION)
    local function loop()
        if M.tick(w, r.time_precise()) then
            r.defer(loop)
        else
            sync.clear()
            if on_exit then on_exit() end
        end
    end
    loop()
    return w
end

return M
