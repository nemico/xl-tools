-- @description X-Laben ドラム（ドラムの音ごとのピッチ・レベル・パン・リバーブ・コーラス・ディレイ）
-- @version 2.7.0
--
-- ミキサー（XLabenMixer）と同じ動き：
--   ・表の値は、曲の頭（セットアップ）の NRPN。変えるのもそこだけ（曲の途中の NRPN は、ピアノロールで手で直す）
--   ・今の位置（再生中は再生位置）で効いている値が曲の頭と違えば、バーの上の ◂ が指す
--   ・動かすと、X-Laben トラックの JSFX から音源へすぐ送る（止まっていても。MIDI アイテムは作らない）
--   ・書くときは、曲の頭の NRPN のまとまりの中だけを、納品の決まり（10 tick おき、MSB 24 → 26 → 28 → 29 → 30 → 31）で
--     並べ直す。まとまりの外のイベントは動かさない。後ろの空きが足りなければ書かずに知らせる
--   ・ピッチ・コーラス・ディレイは既定（64 / 0 / 0）に戻すとイベントを消す。レベル・パン・リバーブは消さない
-- つまみ（丸い。ミキサーと同じ）：上下にドラッグ（Shift で細かく）、ダブルクリックで数字を入れる、Ctrl+クリックで既定の値。
--   ホイールでは値を変えない（表を上下に動かす）
-- 上の帯の「使う音だけ」の右：トラック全体の送り（Rev・Cho・Del。曲の頭の CC91・93・94。ミキサーのつまみと同じ値）
-- 音の名前をクリック：その音だけを鳴らす。▶：その音が打ち込まれた所（どこか1つ）から再生する
-- キー：Space 再生・停止（XLaben_Play と同じ）、Ctrl+Z / Ctrl+Y 元に戻す / やり直し
-- 上の帯を右クリック：ドッキングなど。もう一度実行すると、見えていれば閉じる、ドッキングの裏のタブなら前に出す（ミキサーと同じ）。
-- 画面がアクティブなときも、割り当てたショートカットで閉じる。

local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local SEP = DIR:match("[\\/]") or "/"
local MIXER_DIR = DIR:gsub("[^\\/]+[\\/]$", "") .. "XLabenMixer" .. SEP
local function need(path)
    local f = io.open(path, "rb")
    if not f then
        r.ShowMessageBox("見つかりません：" .. path .. "\nXLabenMixer を、このフォルダのとなりに置いてください。", "X-Laben Drum", 0)
        return nil
    end
    f:close()
    return dofile(path)
end
local proj = need(MIXER_DIR .. "lib/xl_project.lua")
if not proj then return end
local live = dofile(MIXER_DIR .. "lib/xl_live.lua")
local play = dofile(MIXER_DIR .. "lib/xl_play.lua")
local sync = dofile(MIXER_DIR .. "lib/xl_sync.lua")
local keys = dofile(MIXER_DIR .. "lib/xl_keys.lua")
local win = dofile(MIXER_DIR .. "lib/xl_window.lua")
local state = dofile(MIXER_DIR .. "lib/xl_state.lua")
local write = dofile(MIXER_DIR .. "lib/xl_write.lua")
local block = dofile(DIR .. "lib/drum_block.lua")
local dio = dofile(DIR .. "lib/drum_io.lua")
local ok_names, NAMES = pcall(dofile, DIR .. "data/drum_names.lua")
if not ok_names then NAMES = {} end

local TITLE = "X-Laben Drum"
local EXT = "XLabenDrum"
local COLS = block.MSB_ORDER
local AUDITION_S = 0.3   -- 音の名前をクリックしたとき、ノートオフまで

-- ------------------------------------------------------------------ 色（ミキサーと同じ）
local function hex(h, a)
    h = h:gsub("#", "")
    return tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, a or 1
end
local C = {
    bg = {hex("#14161b")}, panel = {hex("#1c1f26")}, strip = {hex("#232730")}, strip2 = {hex("#1e2129")},
    line = {hex("#2e333d")}, text = {hex("#e7e9ee")}, dim = {hex("#8d93a3")}, faint = {hex("#5b6170")},
    accent = {hex("#4ea1ff")}, accent2 = {hex("#9b7bff")}, warn = {hex("#ffad33")}, ok = {hex("#3dd17f")},
    red = {hex("#ff5b5b")}, groove = {hex("#0f1115")}, now = {hex("#ff4fd8")},
}
local function set(c, a) gfx.set(c[1], c[2], c[3], a or c[4] or 1) end

-- ------------------------------------------------------------------ 描く道具（ミキサーと同じ）
local S = 1
local function px(v) return math.floor(v * S + 0.5) end
local function fonts()
    gfx.setfont(1, "Meiryo UI", px(12))
    gfx.setfont(2, "Meiryo UI", px(14))
    gfx.setfont(3, "Meiryo UI", px(14), string.byte("b"))
    gfx.setfont(5, "Meiryo UI", px(16), string.byte("b"))
    gfx.setfont(6, "Meiryo UI", px(12), string.byte("b"))
end
local function rrect(x, y, w, h, rad, fill)
    rad = math.min(rad, w / 2, h / 2)
    if w <= 0 or h <= 0 then return end
    if fill then
        gfx.rect(x + rad, y, w - 2 * rad, h, 1)
        gfx.rect(x, y + rad, rad, h - 2 * rad, 1)
        gfx.rect(x + w - rad, y + rad, rad, h - 2 * rad, 1)
        gfx.circle(x + rad, y + rad, rad, 1, 1)
        gfx.circle(x + w - rad - 1, y + rad, rad, 1, 1)
        gfx.circle(x + rad, y + h - rad - 1, rad, 1, 1)
        gfx.circle(x + w - rad - 1, y + h - rad - 1, rad, 1, 1)
    else
        gfx.roundrect(x, y, w, h, rad, 1)
    end
end
local function fit(s, w)
    if gfx.measurestr(s) <= w then return s end
    local n = utf8.len(s) or #s
    while n > 0 do
        local cut = utf8.offset(s, n) or (n + 1)
        local t = s:sub(1, cut - 1) .. "…"
        if gfx.measurestr(t) <= w then return t end
        n = n - 1
    end
    return "…"
end
local function text(s, x, y, w, h, align, color, font)
    gfx.setfont(font or 2)
    set(color or C.text)
    s = fit(s, w)
    local tw, th = gfx.measurestr(s)
    local tx = align == "c" and x + (w - tw) / 2 or align == "r" and x + w - tw or x
    gfx.x, gfx.y = math.floor(tx), math.floor(y + (h - th) / 2)
    gfx.drawstr(s)
end
local function inside(x, y, w, h)
    return gfx.mouse_x >= x and gfx.mouse_x < x + w and gfx.mouse_y >= y and gfx.mouse_y < y + h
end

-- ------------------------------------------------------------------ 状態
local tracks = {ch = {}}
local drums = {}          -- {{ch, tr, info, times, now_values}, ...}（ch10 → ch9）
local tab = 10            -- 見ているチャンネル
local show_all = false    -- キットの全部の音を出すか（既定：曲で使っている音・まとまりにある音だけ）
local scroll_y = 0
local status, status_until = "", 0
local hover_text = ""
local last_change, last_scan, last_now_read, last_now_t = -1, -100, -100, nil
local drag = nil
local tdrag = nil         -- 上の帯のトラック全体の送り（Rev・Cho・Del）をつまんでいる間
local cache = state.new_cache()   -- トラックの CC（ミキサーと同じ）
-- トラック全体の送り：番号、名前、GS の既定（Ctrl+クリック）
local TRACK_SENDS = {{91, "Rev", 40}, {93, "Cho", 0}, {94, "Del", 0}}
XLABEN_DRUM_LAYOUT = {}   -- 描いた所（テストで使う）
local mouse_was, last_click_t, last_click_id = 0, 0, nil
local audition_off = {}   -- {{ch, note, at}, ...}
local beat_seen, beat_time = -1, 0

local function say(msg, secs) status, status_until = msg, r.time_precise() + (secs or 4) end
local function playing() return r.GetPlayState() & 1 == 1 end
local function now_time() return playing() and r.GetPlayPosition() or r.GetCursorPosition() end

-- ドラムのトラック：上から最初の「A10R…」「A09R…」など（そのチャンネルの名前に R が付いたもの）。無ければ、ミキサーと同じチャンネルのトラック
-- （テンプレートには「A09  …」（ふつうの音色）と「A09R Sub Drum」の両方があり、ふつうのほうが上にある）
-- どのチャンネルがドラムかは、トラックの名前ではなく、SysEx の「Use for Rhythm Part」で決める（xl_project.rhythm_parts）。
-- 前は名前（A09R・Drum）で決めていたので、ch9 がふつうの音色の曲でも ch9 のタブを出していた
local function drum_track(ch)
    local want = string.format("^%%s*A%02dR", ch)
    for i = 0, r.CountTracks(0) - 1 do
        local t = r.GetTrack(0, i)
        if proj.track_name(t):match(want) then return t end
    end
    return tracks.ch[ch]
end

local function current()
    for _, d in ipairs(drums) do if d.ch == tab then return d end end
    return drums[1]
end

local function read_all(force)
    local change = r.GetProjectStateChangeCount(0)
    local now = r.time_precise()
    if not force and change == last_change and now - last_scan < 1.0 then return end
    local changed = force or change ~= last_change
    last_change, last_scan = change, now
    tracks = proj.find_tracks()
    local keep = {}
    for _, d in ipairs(drums) do keep[d.ch] = d end
    drums = {}
    local rhythm = proj.rhythm_parts(tracks)
    local order = {}
    if rhythm[10] then order[1] = 10 end
    for ch = 1, 16 do if ch ~= 10 and rhythm[ch] then order[#order + 1] = ch end end
    for _, ch in ipairs(order) do
        local tr = drum_track(ch)
        if tr then
            local d = keep[ch] or {ch = ch}
            if changed or d.tr ~= tr or not d.info then
                d.tr = tr
                d.info, d.err = dio.read(tr, ch)
                d.times = dio.note_times(tr)
                d.now_values = nil
            end
            drums[#drums + 1] = d
        end
    end
    state.refresh(cache, proj, tracks, changed)
    if changed then last_now_read = -100 end
end

local function kit_names(d)
    local kit = d.info and d.info.kit or {}
    local bank = NAMES[kit.lsb or -1] or NAMES[4] or {}
    return bank[kit.pc or 0] or {name = "", notes = {}}
end

local function note_name(d, note) return kit_names(d).notes[note] or "" end

-- 表に出す音：曲で使っている音と、まとまりにある音（全部の音を出すときは、キットの名前がある音も）
local function rows_of(d)
    local set_, list = {}, {}
    local function add(n) if not set_[n] then set_[n] = true; list[#list + 1] = n end end
    for n in pairs(d.times or {}) do add(n) end
    if d.info and d.info.block then for _, e in ipairs(d.info.block.entries) do add(e.note) end end
    if show_all then for n in pairs(kit_names(d).notes) do add(n) end end
    table.sort(list)
    return list
end

-- ------------------------------------------------------------------ 決まり（納品の決まり：config.yaml の delivery_rules.drums）
local function kind_of(name)
    local low = name:lower()
    if low:find("kick") or name:find("BD") then return "kick" end
    if low:find("snare") or name:find("SD") then return "snare" end
end
-- 外れていれば理由の文字列
local function rule(d, note, msb, v)
    local k = kind_of(note_name(d, note))
    if v == nil then
        if (msb == 26 or msb == 28 or msb == 29) and d.times and d.times[note] then
            return "使う音なのに、まとまりに入っていない"
        end
        return nil
    end
    if k == "kick" then
        if msb == 28 and v ~= 64 then return "キックのパンは 64" end
        if msb == 29 and v ~= 0 then return "キックのリバーブは 0" end
        if msb == 30 and v ~= 0 then return "キックのコーラスは 0" end
    elseif k == "snare" then
        if msb == 28 and v ~= 64 then return "スネアのパンは 64" end
        if msb == 30 and v ~= 0 then return "スネアのコーラスは 0" end
    end
end

-- ------------------------------------------------------------------ 送る・書く
local function p_default(msb) return block.PARAMS[msb] and block.PARAMS[msb].default end

-- NRPN を送り、そのあと音源で選ばれていたものに戻す（曲のデータの次のデータエントリが、違う所に効かないように）
local last_preview = 0
local function preview(ch, msb, note, v, force)
    if not force and r.time_precise() - last_preview < 0.015 then return end
    last_preview = r.time_precise()
    local mode, smsb, slsb = live.selection(ch)
    local st = 0xB0 | (ch - 1)
    live.send(string.char(st, 99, msb), 0)
    live.send(string.char(st, 98, note), 0)
    live.send(string.char(st, 6, v), 0)
    if mode == "rpn" then
        live.send(string.char(st, 101, smsb), 0); live.send(string.char(st, 100, slsb), 0)
    elseif mode == "nrpn" then
        live.send(string.char(st, 99, smsb), 0); live.send(string.char(st, 98, slsb), 0)
    end
end

local function commit(d, msb, note, v)
    if not d.info then return say(d.err or "読めませんでした", 6) end
    local ok, err = dio.write(d.info, msb, note, v)
    if ok then
        local p = block.PARAMS[msb]
        say(string.format("ch%d %d %s の %s を %d に%s", d.ch, note, note_name(d, note), p.name, v,
                          (p.default and v == p.default) and "（既定なので、イベントを消しました）" or ""))
    else
        say("書けませんでした：" .. tostring(err), 8)
        preview(d.ch, msb, note, d.info.values[msb][note] or p_default(msb) or v, true)
    end
    read_all(true)
end

local function ask_value(d, msb, note, cur)
    local p = block.PARAMS[msb]
    local ok, s = r.GetUserInputs(TITLE, 1, string.format("ch%d %d %s の %s（0〜127%s）,extrawidth=80", d.ch, note,
        note_name(d, note), p.name, p.bipolar and "、64 が真ん中" or ""), tostring(cur))
    local v = ok and tonumber(s)
    if v then
        v = math.max(0, math.min(127, math.floor(v + 0.5)))
        preview(d.ch, msb, note, v, true)
        commit(d, msb, note, v)
    end
end

-- 押した瞬間の、Ctrl+クリック（既定の値）とダブルクリック（数字を入れる）。扱ったら true（ミキサーと同じ）
local function click_actions(id, d, msb, note, cur)
    local t_now = r.time_precise()
    if gfx.mouse_cap & 4 == 4 then
        local def = p_default(msb)
        if def then
            preview(d.ch, msb, note, def, true)
            commit(d, msb, note, def)
        else
            say(block.PARAMS[msb].name .. " は音ごとに既定の値が違うので、Ctrl+クリックでは戻せません", 5)
        end
        return true
    end
    if last_click_id == id and t_now - last_click_t < 0.4 then
        last_click_id = nil
        ask_value(d, msb, note, cur)
        return true
    end
    last_click_id, last_click_t = id, t_now
    return false
end

local function pressed_here(x, y, w, h)
    return inside(x, y, w, h) and gfx.mouse_cap & 1 == 1 and mouse_was & 1 == 0 and not drag
end

local function update_drag()
    if not drag then return end
    local down = gfx.mouse_cap & 1 == 1
    if not drag.moved and math.abs(drag.my - gfx.mouse_y) >= px(3) then
        drag.moved, drag.acc, drag.last_my = true, 0, drag.my
    end
    local v = drag.value
    if drag.moved then
        local step = (gfx.mouse_cap & 8 == 8) and 0.15 or (127 / math.max(drag.len, 127))
        drag.acc = drag.acc + (drag.last_my - gfx.mouse_y) * step
        drag.last_my = gfx.mouse_y
        v = math.max(0, math.min(127, math.floor(drag.start_v + drag.acc + 0.5)))
    end
    if v ~= drag.value then
        drag.value = v
        preview(drag.d.ch, drag.msb, drag.note, v)
    end
    if not down then
        local g = drag
        drag = nil
        preview(g.d.ch, g.msb, g.note, g.value, true)
        if g.value ~= g.start_v then commit(g.d, g.msb, g.note, g.value) end
    end
end

-- 音だけ鳴らす（ノートオン、少ししてノートオフ）
local function audition(ch, note)
    live.send(string.char(0x90 | (ch - 1), note, 100), 0)
    audition_off[#audition_off + 1] = {ch = ch, note = note, at = r.time_precise() + AUDITION_S}
end
local function update_audition()
    local now = r.time_precise()
    for i = #audition_off, 1, -1 do
        local a = audition_off[i]
        if now >= a.at then
            live.send(string.char(0x80 | (a.ch - 1), a.note, 64), 0)
            table.remove(audition_off, i)
        end
    end
end

-- その音が打ち込まれた所（どこか1つ、前と同じ所は避ける）から再生する。再生は XLaben_Play と同じ（準備ができてから）
local last_random = {}
local function play_from_hit(d, note)
    local list = d.times and d.times[note]
    if not list or #list == 0 then return say("この音は、曲のどこにも打ち込まれていません", 4) end
    local pool = {}
    for _, t in ipairs(list) do
        if not last_random[note] or math.abs(t - last_random[note]) > 0.01 then pool[#pool + 1] = t end
    end
    if #pool == 0 then pool = list end
    local t = pool[math.random(1, #pool)]
    last_random[note] = t
    if r.GetPlayState() & 5 ~= 0 then r.OnStopButton() end
    r.SetEditCurPos(t, true, false)
    play.toggle(MIXER_DIR)
end

-- ------------------------------------------------------------------ 描く
local function button(label, x, y, w, h, on, color, font)
    local hovered = inside(x, y, w, h)
    if on then set(color or C.accent) else set(hovered and C.line or C.strip2) end
    rrect(x, y, w, h, px(4), true)
    text(label, x, y, w, h, "c", on and {0.05, 0.06, 0.08} or (hovered and C.text or C.dim), font or 3)
    return hovered and not drag and gfx.mouse_cap & 1 == 0 and mouse_was & 1 == 1
end

-- 丸いつまみ。曲の頭の値 v（nil = まとまりに無い）、今の位置の値が違えば輪の上にマゼンタの点。
-- 上下にドラッグ（上で増える）、ダブルクリックで数字、Ctrl+クリックで既定。ホイールでは変えない（ミキサーのつまみと同じ形・動き）
local KNOB_SWEEP = math.rad(135)
local function arc_band(cx, cy, rr, a1, a2, width)
    if a2 < a1 then a1, a2 = a2, a1 end
    if a2 - a1 < 0.01 then return end
    for k = 0, width - 1, 0.5 do gfx.arc(cx, cy, rr - k, a1, a2, 1) end
end
local function angle(v, bipolar)
    if bipolar then return (v - 64) / 63 * KNOB_SWEEP end
    return -KNOB_SWEEP + 2 * KNOB_SWEEP * v / 127
end

-- つまみを描く。v（nil なら値の弧と線なし）、nowv（v と違えば輪の上にマゼンタの点）
local function draw_knob(cx, cy, rr, v, nowv, bipolar, col, hot)
    local ring = px(3)
    set(C.groove)
    arc_band(cx, cy, rr, -KNOB_SWEEP, KNOB_SWEEP, ring)
    if v then
        set(col, hot and 1 or 0.9)
        if bipolar then arc_band(cx, cy, rr, 0, angle(v, true), ring)
        else arc_band(cx, cy, rr, -KNOB_SWEEP, angle(v, false), ring) end
    end
    local br = rr - ring - px(2)
    set(hot and {0.24, 0.27, 0.33} or {0.19, 0.21, 0.26})
    gfx.circle(cx, cy, br, 1, 1)
    set({0.32, 0.35, 0.42})
    gfx.circle(cx, cy, br, 0, 1)
    if v then
        local a = angle(v, bipolar)
        set(col)
        gfx.line(cx + math.sin(a) * br * 0.3, cy - math.cos(a) * br * 0.3, cx + math.sin(a) * br, cy - math.cos(a) * br, 1)
    end
    if nowv and nowv ~= v then
        local a = angle(nowv, bipolar)
        local nr = rr - ring / 2
        local nx, ny = cx + math.sin(a) * nr, cy - math.cos(a) * nr
        set({0.05, 0.05, 0.07})
        gfx.circle(nx, ny, px(4), 1, 1)
        set(C.now)
        gfx.circle(nx, ny, px(3), 1, 1)
    end
end

local function cell(d, note, msb, x, y, w, h)
    local p = block.PARAMS[msb]
    local id = d.ch .. ":" .. note .. ":" .. msb
    local stored = d.info and d.info.values[msb][note]
    local v = stored or p.default
    local hovered = inside(x, y, w, h)
    if pressed_here(x, y, w, h) then
        if not click_actions(id, d, msb, note, v or 64) then
            drag = {d = d, msb = msb, note = note, start_v = v or 64, value = v or 64, my = gfx.mouse_y, len = px(160), moved = false}
        end
    end
    local active = drag and drag.d == d and drag.msb == msb and drag.note == note
    if active then v = drag.value end
    local bad = rule(d, note, msb, v)
    local rr = math.min(px(13), h / 2 - px(1))
    local cx, cy = x + px(8) + rr, y + h / 2
    local col = bad and C.warn or (stored and (p.bipolar and C.accent2 or C.accent) or C.faint)
    local nowv = d.now_values and d.now_values[msb][note]
    draw_knob(cx, cy, rr, v, (nowv and nowv ~= stored) and nowv or nil, p.bipolar, col, hovered or active)
    -- 数字（ピッチ・パンは真ん中からの差も）
    local label = v and tostring(v) or "なし"
    if v and msb == 28 then label = v == 64 and "C" or (v < 64 and ("L" .. (64 - v)) or ("R" .. (v - 64))) end
    if v and msb == 24 then label = v == 64 and "0" or string.format("%+d", v - 64) end
    local color = bad and C.warn or (stored and C.text or C.faint)
    local tx = cx + rr + px(6)
    text(label, tx, y, x + w - tx - px(4), h, "l", color, stored and 3 or 2)
    if hovered then
        local s = string.format("ch%d  %d %s  %s：%s", d.ch, note, note_name(d, note), p.name,
                                stored and tostring(stored) or (p.default and (p.default .. "（既定。イベントなし）") or "まとまりに無い"))
        if nowv and nowv ~= stored then s = s .. string.format("　今の位置：%d", nowv) end
        if bad then s = s .. "　（" .. bad .. "）" end
        hover_text = s
    end
end

-- トラック全体の送り（Rev・Cho・Del）のつまみ：曲の頭の値を変える（ミキサーと同じ：XLabenMixer の xl_write.cc_initial）。
-- 上下にドラッグ（Shift で細かく）、ダブルクリックで数字、Ctrl+クリックで既定。ホイールでは変えない
local last_cc_preview = 0
local function preview_cc(ch, num, v, force)
    if force or r.time_precise() - last_cc_preview > 0.015 then
        live.cc(ch, num, v)
        last_cc_preview = r.time_precise()
    end
end
local function commit_cc(d, num, name, v)
    local ok, err = write.cc_initial(d.tr, d.ch, num, v)
    if ok then say(string.format("ch%d %s（トラック全体）の初期値を %d に", d.ch, name, v))
    else say("書けませんでした：" .. tostring(err), 8) end
    read_all(true)
end

local function track_send(d, num, name, default, x, y, w, h)
    local init = state.initial(cache, d.ch, num, default)
    local nowv = state.values(cache, d.ch, now_time())[num]
    local rr = px(12)
    local cx, cy = x + px(2) + rr, y + h / 2
    local hit_x, hit_y, hit_w, hit_h = cx - rr, cy - rr, w - px(4), 2 * rr
    local hovered = inside(hit_x, hit_y, hit_w, hit_h)
    local id = "send:" .. d.ch .. ":" .. num
    if hovered and gfx.mouse_cap & 1 == 1 and mouse_was & 1 == 0 and not drag and not tdrag then
        local t_now = r.time_precise()
        if gfx.mouse_cap & 4 == 4 then
            preview_cc(d.ch, num, default, true)
            commit_cc(d, num, name, default)
        elseif last_click_id == id and t_now - last_click_t < 0.4 then
            last_click_id = nil
            local ok, s = r.GetUserInputs(TITLE, 1, string.format("ch%d %s（トラック全体、0〜127）,extrawidth=60", d.ch, name), tostring(init))
            local v = ok and tonumber(s)
            if v then
                v = math.max(0, math.min(127, math.floor(v + 0.5)))
                preview_cc(d.ch, num, v, true)
                commit_cc(d, num, name, v)
            end
        else
            last_click_id, last_click_t = id, t_now
            tdrag = {d = d, num = num, name = name, start_v = init, value = init, my = gfx.mouse_y, moved = false}
        end
    end
    local v = (tdrag and tdrag.d == d and tdrag.num == num) and tdrag.value or init
    draw_knob(cx, cy, rr, v, nowv ~= v and nowv or nil, false, C.accent, hovered or (tdrag and tdrag.num == num))
    -- 名前と値は、つまみの右に上下 2 段（帯の中に収める）
    local tx, tw = cx + rr + px(5), x + w - (cx + rr + px(5)) - px(2)
    text(name, tx, cy - px(15), tw, px(15), "l", C.dim, 1)
    text(tostring(v), tx, cy, tw, px(15), "l", C.text, 6)
    if hovered then
        hover_text = string.format("ch%d %s（トラック全体の送り）：曲の頭 %d%s", d.ch, name, v,
                                   (nowv and nowv ~= v) and string.format("　今の位置 %d", nowv) or "")
    end
end

local function update_tdrag()
    if not tdrag then return end
    local g = tdrag
    if not g.moved and math.abs(g.my - gfx.mouse_y) >= px(3) then g.moved, g.acc, g.last_my = true, 0, g.my end
    if g.moved then
        local step = (gfx.mouse_cap & 8 == 8) and 0.15 or (127 / px(160))
        g.acc = g.acc + (g.last_my - gfx.mouse_y) * step
        g.last_my = gfx.mouse_y
        local v = math.max(0, math.min(127, math.floor(g.start_v + g.acc + 0.5)))
        if v ~= g.value then g.value = v; preview_cc(g.d.ch, g.num, v) end
    end
    if gfx.mouse_cap & 1 == 0 then
        tdrag = nil
        preview_cc(g.d.ch, g.num, g.value, true)
        if g.value ~= g.start_v then commit_cc(g.d, g.num, g.name, g.value) end
    end
end

local function header(W)
    local hh = px(48)
    gfx.gradrect(0, 0, W, hh, C.panel[1], C.panel[2], C.panel[3], 1, 0, 0, 0, 0, -0.0004, -0.0004, -0.0003, 0)
    set(C.line)
    gfx.line(0, hh - 1, W, hh - 1)
    text("X-Laben Drum", px(14), 0, px(140), hh, "l", C.text, 5)
    local bx, by, bs = px(150), px(10), px(28)
    local hov = inside(bx, by, bs, bs)
    set(hov and C.line or C.strip)
    rrect(bx, by, bs, bs, px(5), true)
    if playing() then
        set(C.text)
        gfx.rect(bx + px(9), by + px(9), px(10), px(10), 1)
    else
        set(C.ok)
        gfx.triangle(bx + px(10), by + px(7), bx + px(10), by + px(21), bx + px(21), by + px(14))
    end
    if hov and not drag and gfx.mouse_cap & 1 == 0 and mouse_was & 1 == 1 then play.toggle(MIXER_DIR) end
    -- チャンネルのタブ（キットの名前）
    local x = bx + bs + px(14)
    XLABEN_DRUM_LAYOUT.tabs = {}
    for _, d in ipairs(drums) do
        XLABEN_DRUM_LAYOUT.tabs[d.ch] = proj.track_name(d.tr)
        local label = string.format("ch%d  %s", d.ch, kit_names(d).name ~= "" and kit_names(d).name or "（キット不明）")
        gfx.setfont(3)
        local w = math.min(px(320), gfx.measurestr(label) + px(24))
        if button(label, x, px(10), w, px(28), d.ch == tab, C.accent, 3) then tab = d.ch; scroll_y = 0 end
        x = x + w + px(8)
    end
    if button(show_all and "キットの全部の音" or "使う音だけ", x + px(6), px(10), px(140), px(28), show_all, C.accent2, 6) then
        show_all = not show_all
        r.SetExtState(EXT, "show_all", show_all and "1" or "0", true)
    end
    x = x + px(156)
    -- トラック全体の送り（曲の頭の CC91・93・94。ミキサーのつまみと同じ）。3 つを詰めて並べる
    local d = current()
    if d then
        XLABEN_DRUM_LAYOUT.sends = {}
        for _, s in ipairs(TRACK_SENDS) do
            track_send(d, s[1], s[2], s[3], x, 0, px(60), hh)
            XLABEN_DRUM_LAYOUT.sends[s[1]] = {x = x + px(2) + px(12), y = hh / 2}
            x = x + px(60)
        end
    end
    if x < W - px(480) then
        text("つまみ：曲の頭の値　マゼンタの点：今の位置の値", W - px(470), 0, px(330), hh, "r", C.faint, 1)
    end
    local beat = live.heartbeat()
    if beat ~= beat_seen then beat_seen, beat_time = beat, r.time_precise() end
    local ok_ = r.time_precise() - beat_time < 0.6
    -- 試聴の様子：つまみと重ならない所に（場所が無ければ点だけ、それも無ければ出さない）
    local jx = math.max(W - px(120), x + px(14))
    XLABEN_DRUM_LAYOUT.sends_end, XLABEN_DRUM_LAYOUT.status_x = x, (jx + px(8) <= W) and (jx - px(5)) or nil
    if jx + px(8) <= W then
        set(ok_ and C.ok or C.faint)
        gfx.circle(jx, hh / 2, px(5), 1, 1)
        if jx + px(90) <= W then text(ok_ and "試聴 OK" or "試聴 待機中", jx + px(10), 0, W - jx - px(12), hh, "l", C.dim, 1) end
    end
    if inside(0, 0, W, gfx.h) and gfx.mouse_cap & 2 == 0 and mouse_was & 2 == 2 and not drag and not tdrag then
        gfx.x, gfx.y = gfx.mouse_x, gfx.mouse_y
        local dock = gfx.dock(-1)
        local sel = gfx.showmenu((dock & 1 == 1 and "!" or "") .. "ドッキング|大きさを戻す|プロジェクトを読み直す")
        if sel == 1 then gfx.dock(dock & 1 == 1 and 0 or 1)
        elseif sel == 2 then gfx.init("", 860, 620)
        elseif sel == 3 then
            proj.clear_cache()
            cache = state.new_cache()
            drums = {}
            read_all(true)
            say(string.format("読み直しました：ドラムのパート %d", #drums))
        end
    end
    return hh
end

local function table_view(d, top, W, H)
    local pad = px(10)
    local name_w = math.max(px(220), math.floor((W - 2 * pad) * 0.32))
    local col_w = math.max(px(70), math.floor((W - 2 * pad - name_w) / #COLS))
    local rh = px(34)
    -- 見出し
    text("音", pad + px(34), top, name_w, px(22), "l", C.dim, 6)
    for i, msb in ipairs(COLS) do
        local p = block.PARAMS[msb]
        text(p.name, pad + name_w + (i - 1) * col_w + px(8), top, col_w - px(12), px(22), "l", C.dim, 6)
    end
    local list_top = top + px(24)
    local area_h = H - list_top
    local rows = rows_of(d)
    local total = #rows * rh
    local max_scroll = math.max(0, total - area_h)
    scroll_y = math.max(0, math.min(max_scroll, scroll_y))
    local t = now_time()
    gfx.setfont(2)
    for i, note in ipairs(rows) do
        local y = list_top + (i - 1) * rh - scroll_y
        if y + rh > list_top and y < H then
            set(i % 2 == 0 and C.strip2 or C.strip)
            gfx.rect(pad, y, W - 2 * pad, rh - px(2), 1)
            -- 鳴った音は光る
            local g = playing() and dio.glow(d.times and d.times[note], r.GetPlayPosition()) or 0
            if g > 0 then
                set(C.red, 0.85 * g)
                gfx.rect(pad, y, name_w, rh - px(2), 1)
            end
            -- ▶：打ち込まれた所から再生
            local px0 = pad + px(4)
            local has_hits = d.times and d.times[note] and #d.times[note] > 0
            local hov_play = inside(px0, y + px(4), px(24), rh - px(10))
            set(hov_play and C.line or C.strip2)
            rrect(px0, y + px(4), px(24), rh - px(10), px(4), true)
            set(has_hits and (hov_play and C.ok or C.dim) or C.faint)
            local cy = y + (rh - px(2)) / 2
            gfx.triangle(px0 + px(8), cy - px(5), px0 + px(8), cy + px(5), px0 + px(17), cy)
            if hov_play and not drag and gfx.mouse_cap & 1 == 0 and mouse_was & 1 == 1 then play_from_hit(d, note) end
            if hov_play then hover_text = "▶：この音が打ち込まれた所（どこか1つ）から再生" end
            -- 音の名前：クリックで鳴らす
            local nx = px0 + px(30)
            local hov_name = inside(nx, y, name_w - (nx - pad), rh - px(2))
            text(string.format("%3d", note), nx, y, px(34), rh - px(2), "r", C.dim, 6)
            text(note_name(d, note), nx + px(42), y, name_w - (nx - pad) - px(46), rh - px(2), "l",
                 (d.times and d.times[note]) and C.text or C.faint, hov_name and 3 or 2)
            if hov_name and gfx.mouse_cap & 1 == 1 and mouse_was & 1 == 0 and not drag then audition(d.ch, note) end
            if hov_name then hover_text = string.format("ch%d %d %s　クリックで鳴らす", d.ch, note, note_name(d, note)) end
            for c, msb in ipairs(COLS) do
                cell(d, note, msb, pad + name_w + (c - 1) * col_w, y + px(2), col_w - px(4), rh - px(6))
            end
        end
    end
    -- 列の間の縦の線（音の名前とピッチ、ピッチとレベル…の境目）
    set(C.line)
    for c = 0, #COLS - 1 do
        local lx = pad + name_w + c * col_w - px(3)
        gfx.line(lx, top, lx, math.min(H, list_top + total - scroll_y))
    end
    -- ホイールは表を上下に動かすだけ（値は変えない）
    if inside(0, list_top, W, area_h) and gfx.mouse_wheel ~= 0 and not drag then
        scroll_y = math.max(0, math.min(max_scroll, scroll_y - gfx.mouse_wheel / 120 * rh * 2))
        gfx.mouse_wheel = 0
    end
    if #rows == 0 then
        text(d.err or "このトラックには、音もドラムの NRPN もありません", pad, list_top + px(10), W - 2 * pad, px(30), "l", C.dim, 2)
    end
    if max_scroll > 0 then
        local sbx = W - px(6)
        set(C.groove)
        rrect(sbx, list_top, px(4), area_h, px(2), true)
        set(C.faint)
        rrect(sbx, list_top + area_h * scroll_y / total, px(4), area_h * area_h / total, px(2), true)
    end
end

-- ------------------------------------------------------------------ 1コマ
local bindings, bindings_read = {}, -100
local own_names = keys.own_names(r)
local other_bindings = {}  -- ミキサー・EFXUI に割り当てたショートカット（この画面で押されたら、そちらを実行）
local function read_bindings()
    bindings_read = r.time_precise()
    local f = io.open(r.GetResourcePath() .. "/reaper-kb.ini", "rb")
    if not f then return end
    local s = f:read("*a")
    f:close()
    bindings, other_bindings = keys.load(s, "drum", own_names)
end

local function save_window()
    local dock, x, y, w, h = gfx.dock(-1, 0, 0, 0, 0)
    r.SetExtState(EXT, "window", string.format("%d,%d,%d,%d,%d", w, h, dock, x, y), true)
end

local last_save = 0
local function frame()
    local key = gfx.getchar()
    if key < 0 then return end
    win.track()
    local now = r.time_precise()
    S = math.max(0.75, math.min(1.6, gfx.h / 620))
    fonts()
    if now - bindings_read > 3 then read_bindings() end
    if key > 0 and keys.matches(bindings, key, gfx.mouse_cap) then
        gfx.quit()
        return
    end
    -- ミキサー・EFXUI のショートカット：REAPER と同じく、そのスクリプトを実行（開く・前に出す・閉じる）
    if key > 0 and keys.run_other(r, other_bindings, key, gfx.mouse_cap) then key = 0 end
    if key == 32 then play.toggle(MIXER_DIR)
    elseif key == 26 then r.Main_OnCommand(40029, 0); read_all(true)
    elseif key == 25 then r.Main_OnCommand(40030, 0); read_all(true) end

    read_all(false)
    local d = current()
    -- 今の位置の値（0.1 秒ごと、位置が変わったとき）
    local t = now_time()
    if d and (now - last_now_read > 0.1) and (t ~= last_now_t or not d.now_values) then
        for _, x in ipairs(drums) do x.now_values = dio.values_at(proj, x.tr, x.ch, t) end
        last_now_read, last_now_t = now, t
    end

    local W, H = gfx.w, gfx.h
    set(C.bg)
    gfx.rect(0, 0, W, H, 1)
    hover_text = ""
    local hh = header(W)
    local sh = px(26)
    if d then
        table_view(d, hh + px(8), W, H - sh)
    else
        text("ドラムのパートがありません（ch10 をふつうの音色にしている、またはトラックが無い）", px(14), hh + px(14), W - px(28), px(30), "l", C.dim, 2)
    end
    update_drag()
    update_tdrag()
    update_audition()
    gfx.mouse_wheel = 0
    set(C.panel)
    gfx.rect(0, H - sh, W, sh, 1)
    local msg = now < status_until and status or hover_text
    text(msg, px(12), H - sh, W * 0.65, sh, "l", (now >= status_until and msg:find("（")) and C.warn or C.dim, 1)
    text(r.GetExtState("XLabenMixer", "last_play"), W * 0.65, H - sh, W * 0.35 - px(12), sh, "r", C.faint, 1)
    mouse_was = gfx.mouse_cap
    if now - last_save > 2 then save_window(); last_save = now end
    gfx.update()
    r.defer(frame)
end

local function cleanup()
    for _, a in ipairs(audition_off) do live.send(string.char(0x80 | (a.ch - 1), a.note, 64), 0) end
    win.finish(EXT)
    save_window()
end

local function main()
    -- もう一度実行されたら：見えていれば閉じる、ドッキングの裏に隠れていれば前に出す（ミキサーと同じ）
    if not win.start(EXT) then return end
    show_all = r.GetExtState(EXT, "show_all") == "1"
    math.randomseed(math.floor(r.time_precise() * 1000))
    read_all(true)
    if tracks.xlaben then
        local ok, err = proj.ensure_jsfx(tracks.xlaben, MIXER_DIR)
        if not ok then say(err, 10) end
    else
        say("「X-Laben」トラックが見つからないので、試聴はできません", 10)
    end
    sync.launch(MIXER_DIR)
    local w, h, dock, x, y = (r.GetExtState(EXT, "window")):match("(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+)")
    gfx.ext_retina = 1
    gfx.init(TITLE, tonumber(w) or 860, tonumber(h) or 620, tonumber(dock) or 0, tonumber(x) or 140, tonumber(y) or 140)
    r.atexit(cleanup)
    frame()
end

main()
