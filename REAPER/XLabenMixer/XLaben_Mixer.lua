-- @description X-Laben ミキサー（16ch。Expression・パン・Rev/Cho/Del・EFX の割り当て・メーター）
-- @version 1.11.0
--
-- 普通の DAW のミキサーのように動く：
--   ・再生位置（止まっていれば編集カーソル）で効いている値をリアルタイムに表示する（読むだけ。DAW の「読む」）
--   ・つまみを動かすと、X-Laben トラックの JSFX から音源へすぐ送る（MIDI アイテムは作らない）
--   ・書くのは曲の頭（セットアップ）の値だけ。曲の途中の変化は、ピアノロールで手で描く
--   ・Expression のフェーダーと下の数字は、曲の頭の Expression。今の位置の Expression は、フェーダーの右の ◂ が指す
--   ・ボリューム（CC7）は 100 固定（歌は 80）。表示だけ
--   ・納品の決まりから外れた値は橙色。チャンネルにマウスを載せると、下に理由が出る
-- キー：Space 再生・停止（XLaben_Play と同じ）、Ctrl+Z / Ctrl+Y 元に戻す / やり直し
-- つまみ：ドラッグ（Shift で細かく：約 7 ピクセルで 1）、ホイール（Ctrl で 5 ずつ）、Ctrl+クリックで既定の値、
--         ダブルクリック：フェーダーは 100 に、数字・バーは数字を入れる
-- Shift+ホイールで、チャンネルの列を横にずらす（ウィンドウが狭いとき）
-- ウィンドウの大きさは自由に変えられる。上の帯を右クリックすると、ドッキングなど。
-- もう一度実行すると：見えていれば閉じる、ドッキングの裏のタブなら前に出す（ドラム・EFXUI と同じ。ツールバーに置くと、開いている間ボタンが点灯する）。
-- ミキサーの画面がアクティブなときも、このスクリプトに割り当てたショートカットで閉じる（REAPER のキーの割り当てを読む）。

local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local proj = dofile(DIR .. "lib/xl_project.lua")
local live = dofile(DIR .. "lib/xl_live.lua")
local play = dofile(DIR .. "lib/xl_play.lua")
local state = dofile(DIR .. "lib/xl_state.lua")
local write = dofile(DIR .. "lib/xl_write.lua")
local rules = dofile(DIR .. "lib/xl_rules.lua")
local sysex = dofile(DIR .. "lib/xl_sysex.lua")
local keys = dofile(DIR .. "lib/xl_keys.lua")
local win = dofile(DIR .. "lib/xl_window.lua")
local sync = dofile(DIR .. "lib/xl_sync.lua")
local ok_tones, tones = pcall(dofile, DIR .. "data/tones.lua")
if not ok_tones then tones = {tone = {}, kit = {}} end

local TITLE = "X-Laben Mixer"
local EXT = "XLabenMixer"
local ORDER = {10, 2, 3, 4, 5, 6, 7, 8, 9, 1, 11, 12, 13, 14, 15, 16}
local DEFAULTS = {[11] = 100, [10] = 64, [91] = 40, [93] = 0, [94] = 0}
local FADER_DOUBLE_CLICK = 100   -- フェーダー（Expression）をダブルクリックしたときの値

-- ------------------------------------------------------------------ 色
local function hex(h, a)
    h = h:gsub("#", "")
    return tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, a or 1
end
local C = {
    bg = {hex("#14161b")}, panel = {hex("#1c1f26")}, strip = {hex("#232730")}, strip2 = {hex("#1e2129")},
    line = {hex("#2e333d")}, text = {hex("#e7e9ee")}, dim = {hex("#8d93a3")}, faint = {hex("#5b6170")},
    accent = {hex("#4ea1ff")}, accent2 = {hex("#9b7bff")}, warn = {hex("#ffad33")}, ok = {hex("#3dd17f")},
    red = {hex("#ff5b5b")}, yellow = {hex("#ffd84a")}, groove = {hex("#0f1115")}, cap = {hex("#d9dde6")},
    now = {hex("#ff4fd8")},
}
local PALETTE = {"#aad2e8", "#415ea6", "#e09f98", "#6bbed5", "#ddbb9f", "#dbc9a4", "#dad8a5", "#d7e4a5",
                 "#f9d004", "#ad273c", "#aecce8", "#b1c5e9", "#b5bde8", "#bcb7e8", "#c0b3ea", "#c9aeec"}
local function set(c, a) gfx.set(c[1], c[2], c[3], a or c[4] or 1) end

-- ------------------------------------------------------------------ 描く道具
local S = 1  -- 大きさの倍率（ウィンドウの高さから）
local function px(v) return math.floor(v * S + 0.5) end

local function fonts()
    gfx.setfont(1, "Meiryo UI", px(12))
    gfx.setfont(2, "Meiryo UI", px(14))
    gfx.setfont(3, "Meiryo UI", px(14), string.byte("b"))
    gfx.setfont(4, "Meiryo UI", px(21), string.byte("b"))
    gfx.setfont(5, "Meiryo UI", px(16), string.byte("b"))
    gfx.setfont(6, "Meiryo UI", px(12), string.byte("b"))
end

local function rrect(x, y, w, h, rad, fill)
    rad = math.min(rad, w / 2, h / 2)
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

-- 幅に収まるように後ろを「…」にする（UTF-8 の文字単位）
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
local cache = state.new_cache()
local rhythm = {[10] = true}   -- ドラムのパート（rescan で SysEx から読む）
local project = nil
local meter_level, meter_count = {}, {}
for ch = 1, 16 do meter_level[ch], meter_count[ch] = 0, nil end
local scroll_x = 0
local status, status_until = "", 0
local hover_ch = nil
local beat_seen, beat_time = -1, 0
local last_tracks_scan = 0
local last_frame = r.time_precise()

-- つまんでいるもの：{id, ch, field, start_v, value, start_mx, start_my, len, t0, points, last_t}
local drag = nil
local mouse_was, last_click_t, last_click_id = 0, 0, nil
-- ホイールで変えて、少し止まったら書く
local wheel_pending = nil

local function say(msg, secs)
    status, status_until = msg, r.time_precise() + (secs or 4)
end

local function now_time()
    if r.GetPlayState() & 1 == 1 then return r.GetPlayPosition() end
    return r.GetCursorPosition()
end

local function playing() return r.GetPlayState() & 1 == 1 end

local log = dofile(DIR .. "lib/xl_log.lua")

-- 右クリックの「プロジェクトを読み直す」：覚えているものを全部捨て、トラックを探し直し、
-- 16 チャンネル＋SysEx の 17 トラックの MIDI を全部読み直す。何を読んだかを下の帯と記録（logs/play_log.txt）に出す
local function reload_all()
    proj.clear_cache()
    cache = state.new_cache()
    tracks = proj.find_tracks()
    rhythm = proj.rhythm_parts(tracks)
    last_tracks_scan = r.time_precise()
    state.refresh(cache, proj, tracks, true)
    local found, missing, lines = 0, {}, {}
    for ch = 1, 16 do
        local tr = tracks.ch[ch]
        if tr then
            found = found + 1
            local items = proj.midi_items(tr)
            local n = 0
            for _ in pairs((cache.ch[ch] or {}).cc or {}) do n = n + 1 end
            lines[#lines + 1] = string.format("ch%d %s：アイテム %d、CC の種類 %d", ch, proj.track_name(tr), #items, n)
        else
            missing[#missing + 1] = tostring(ch)
        end
    end
    local sys_items = tracks.sysex and #proj.midi_items(tracks.sysex) or 0
    lines[#lines + 1] = tracks.sysex and string.format("SysEx %s：アイテム %d", proj.track_name(tracks.sysex), sys_items) or "SysEx のトラックなし"
    log.write("Mixer", "読み直した：%d チャンネル＋%s", found, tracks.sysex and "SysEx" or "SysEx なし")
    for _, l in ipairs(lines) do log.write("Mixer", "  %s", l) end
    say(string.format("読み直しました：%d チャンネル%s%s", found, tracks.sysex and "＋SysEx" or "（SysEx のトラックなし）",
                      #missing > 0 and ("。トラックが無いチャンネル：" .. table.concat(missing, ", ")) or ""), 6)
end

local function rescan(force)
    local p = r.EnumProjects(-1)
    if p ~= project then
        project = p
        cache = state.new_cache()
        force = true
    end
    if force or r.time_precise() - last_tracks_scan > 1.0 then
        tracks = proj.find_tracks()
        rhythm = proj.rhythm_parts(tracks)
        last_tracks_scan = r.time_precise()
    end
    state.refresh(cache, proj, tracks, force)
end

local function track_name(ch)
    local tr = tracks.ch[ch]
    return tr and proj.short_name(proj.track_name(tr)) or ""
end

-- ドラムのパートか：SysEx の「Use for Rhythm Part」で決める（トラックの名前ではない。xl_project.rhythm_parts）
local function is_drum(ch) return rhythm[ch] == true end

local function program_name(ch, prog)
    if not prog then return "（音色の指定なし）" end
    local msb, lsb, pc = prog[1], prog[2], prog[3]
    if is_drum(ch) then
        return tones.kit[lsb .. ":" .. pc] or tones.kit["0:" .. pc] or string.format("Drum %d", pc + 1)
    end
    return tones.tone[msb .. ":" .. lsb .. ":" .. pc] or string.format("PC%d  %d/%d", pc + 1, msb, lsb)
end

-- ------------------------------------------------------------------ 書く
local function after_write(ok, err, msg)
    if ok then say(msg) else say("書けませんでした：" .. tostring(err), 6) end
    rescan(true)
end

-- 曲の頭（セットアップ）の値を書く
local function commit_cc(ch, num, v)
    local tr = tracks.ch[ch]
    if not tr then return end
    local name = ({[11] = "Expression", [10] = "パン", [91] = "Rev", [93] = "Cho", [94] = "Del"})[num] or ("CC" .. num)
    local ok, err = write.cc_initial(tr, ch, num, v)
    after_write(ok, err, string.format("ch%d %s の初期値を %d に", ch, name, v))
    local now = state.values(cache, ch, now_time())[num]
    if ok and now ~= v then
        say(string.format("ch%d %s の初期値を %d に（今の位置では、あとの CC の %d が効いています）", ch, name, v, now), 6)
    end
end

local function commit_efx(ch, v)
    live.send(sysex.efx_assign_msg(ch, v))
    if not tracks.sysex then return say("SysEx のトラック（A01 SYSTEM_EXCLUSIVE）がありません", 6) end
    local label = ({[0] = "オフ", "A", "B"})[v]
    local ok, err = write.efx_initial(tracks.sysex, ch, v)
    after_write(ok, err, string.format("ch%d の EFX の初期値を %s に", ch, label))
end

local last_preview = 0
local function preview(ch, num, v, force)
    if force or r.time_precise() - last_preview > 0.015 then
        live.cc(ch, num, v)
        last_preview = r.time_precise()
    end
end

-- ------------------------------------------------------------------ つまみの共通の動き
local CC_NAME = {[11] = "Expression", [10] = "パン", [91] = "Rev", [93] = "Cho", [94] = "Del"}

-- 数字を入れてもらって書く
local function ask_value(ch, num, cur)
    local label = string.format("ch%d %s（%s）", ch, CC_NAME[num] or ("CC" .. num), num == 10 and "0〜127、64 が真ん中" or "0〜127")
    local ok, s = r.GetUserInputs(TITLE, 1, label .. ",extrawidth=60", tostring(cur))
    local v = ok and tonumber(s)
    if v then
        v = math.max(0, math.min(127, math.floor(v + 0.5)))
        preview(ch, num, v, true)
        commit_cc(ch, num, v)
    end
end

-- 押した瞬間の、Ctrl+クリック（初期の値）とダブルクリック（数字を入れる）。扱ったら true
local function click_actions(id, ch, num, cur, double_value)
    local t_now = r.time_precise()
    if gfx.mouse_cap & 4 == 4 then
        local v = DEFAULTS[num] or cur
        preview(ch, num, v, true)
        commit_cc(ch, num, v)
        return true
    end
    if last_click_id == id and t_now - last_click_t < 0.4 then
        last_click_id = nil
        if double_value then
            preview(ch, num, double_value, true)
            commit_cc(ch, num, double_value)
        else
            ask_value(ch, num, cur)
        end
        return true
    end
    last_click_id, last_click_t = id, t_now
    return false
end

local function pressed_here(x, y, w, h)
    return inside(x, y, w, h) and gfx.mouse_cap & 1 == 1 and mouse_was & 1 == 0 and not drag
end

-- 数字の枠：ダブルクリックで数字を入れる、Ctrl+クリックで初期の値
local function value_box(id, ch, num, cur, x, y, w, h)
    if pressed_here(x, y, w, h) then click_actions(id, ch, num, cur) end
end

-- kind: "v"（縦）/ "h"（横）。len はつまみの長さ（ピクセル）
-- double_value：ダブルクリックでこの値にする（nil なら数字を入れる）
local function control(id, ch, num, cur, x, y, w, h, kind, len, double_value)
    local hovered = inside(x, y, w, h)
    if pressed_here(x, y, w, h) then
        if click_actions(id, ch, num, cur, double_value) then return cur, hovered end
        drag = {id = id, ch = ch, num = num, start_v = cur, value = cur, mx = gfx.mouse_x, my = gfx.mouse_y,
                len = len, kind = kind, moved = false}
        live.hold_cc(ch, num)
    end
    if hovered and not drag and gfx.mouse_wheel ~= 0 and gfx.mouse_cap & 8 == 0 then
        local step = (gfx.mouse_cap & 4 == 4) and 5 or 1
        local v = math.max(0, math.min(127, cur + (gfx.mouse_wheel > 0 and step or -step)))
        gfx.mouse_wheel = 0
        preview(ch, num, v, true)
        wheel_pending = {ch = ch, num = num, value = v, at = r.time_precise()}
        return v, hovered
    end
    if wheel_pending and wheel_pending.ch == ch and wheel_pending.num == num then return wheel_pending.value, hovered end
    if drag and drag.id == id then return drag.value, true end
    return cur, hovered
end

local function update_drag()
    if not drag then return end
    local down = gfx.mouse_cap & 1 == 1
    if not drag.moved then
        -- クリックだけ（ダブルクリックの1回目など）で値が変わらないように、少し動かすまで待つ
        local delta = drag.kind == "v" and (drag.my - gfx.mouse_y) or (gfx.mouse_x - drag.mx)
        if math.abs(delta) >= px(3) then
            drag.moved = true
            drag.acc, drag.last_mx, drag.last_my = 0, drag.mx, drag.my  -- 押した所から数える
        end
    end
    local v = drag.value
    if drag.moved then
        -- 1ピクセルあたりの量：ふつうはつまみの長さで 0〜127（短いものは 1 ピクセルで 1）、Shift を押している間は 0.15
        local step = (gfx.mouse_cap & 8 == 8) and 0.15 or (127 / math.max(drag.len, 127))
        local d = drag.kind == "v" and (drag.last_my - gfx.mouse_y) or (gfx.mouse_x - drag.last_mx)
        drag.last_mx, drag.last_my = gfx.mouse_x, gfx.mouse_y
        drag.acc = drag.acc + d * step
        v = math.max(0, math.min(127, math.floor(drag.start_v + drag.acc + 0.5)))
    end
    if v ~= drag.value then
        drag.value = v
        preview(drag.ch, drag.num, v)
    end
    if not down then
        local d = drag
        drag = nil
        live.hold_cc(nil)
        preview(d.ch, d.num, d.value, true)
        if d.value ~= d.start_v then commit_cc(d.ch, d.num, d.value) end
    end
end

local function update_wheel()
    if wheel_pending and r.time_precise() - wheel_pending.at > 0.6 then
        local w = wheel_pending
        wheel_pending = nil
        commit_cc(w.ch, w.num, w.value)
    end
end

-- ------------------------------------------------------------------ 描く：部品
local function button(label, x, y, w, h, on, color, font)
    local hovered = inside(x, y, w, h)
    if on then set(color or C.accent) else set(hovered and C.line or C.strip2) end
    rrect(x, y, w, h, px(4), true)
    text(label, x, y, w, h, "c", on and {0.05, 0.06, 0.08} or (hovered and C.text or C.dim), font or 3)
    return hovered and not drag and gfx.mouse_cap & 1 == 0 and mouse_was & 1 == 1
end

-- 横のバー（rev / cho / del）。値の分だけ塗る
-- 丸いつまみ。左下（7時）から右下（5時）まで 270 度。bipolar（パン）は真上から左右に塗る。
-- 上下にドラッグ（上で増える）、ホイール、ダブルクリックで数字を入れる、Ctrl+クリックで既定の値
local KNOB_SWEEP = math.rad(135)
local function arc_band(cx, cy, r, a1, a2, width)
    if a2 < a1 then a1, a2 = a2, a1 end
    if a2 - a1 < 0.01 then return end
    for k = 0, width - 1, 0.5 do gfx.arc(cx, cy, r - k, a1, a2, 1) end
end

-- cur：つまみの値（曲の頭の値）、now：今の位置の値（違えば、輪の上にマゼンタの点で示す）
local function knob(id, ch, num, label, cur, now, x, y, w, bad, bipolar)
    local r = math.min(px(17), (w - px(6)) / 2)
    local cx, cy = x + w / 2, y + r + px(2)
    local v, hovered = control(id, ch, num, cur, cx - r, cy - r, 2 * r, 2 * r, "v", px(160))
    local ring = px(3)
    -- 溝
    set(C.groove)
    arc_band(cx, cy, r, -KNOB_SWEEP, KNOB_SWEEP, ring)
    -- 値
    local col = bad and C.warn or (bipolar and C.accent2 or C.accent)
    set(col, hovered and 1 or 0.9)
    if bipolar then
        arc_band(cx, cy, r, 0, (v - 64) / 63 * KNOB_SWEEP, ring)
    else
        arc_band(cx, cy, r, -KNOB_SWEEP, -KNOB_SWEEP + 2 * KNOB_SWEEP * v / 127, ring)
    end
    -- つまみの本体と、向きの線
    local br = r - ring - px(3)
    set(hovered and {0.24, 0.27, 0.33} or {0.19, 0.21, 0.26})
    gfx.circle(cx, cy, br, 1, 1)
    set({0.32, 0.35, 0.42})
    gfx.circle(cx, cy, br, 0, 1)
    local ang = bipolar and ((v - 64) / 63 * KNOB_SWEEP) or (-KNOB_SWEEP + 2 * KNOB_SWEEP * v / 127)
    set(col)
    gfx.line(cx + math.sin(ang) * br * 0.35, cy - math.cos(ang) * br * 0.35,
             cx + math.sin(ang) * br, cy - math.cos(ang) * br, 1)
    -- 今の位置の値（曲の頭と違うとき）：輪の上にマゼンタの点（暗い縁）
    if now and now ~= v then
        local na = bipolar and ((now - 64) / 63 * KNOB_SWEEP) or (-KNOB_SWEEP + 2 * KNOB_SWEEP * now / 127)
        local nr = r - ring / 2
        local nx, ny = cx + math.sin(na) * nr, cy - math.cos(na) * nr
        set({0.05, 0.05, 0.07})
        gfx.circle(nx, ny, px(4), 1, 1)
        set(C.now)
        gfx.circle(nx, ny, px(3), 1, 1)
    end
    -- 下：名前と値
    local ty = cy + r + px(1)
    local shown = tostring(v)
    if bipolar then shown = v == 64 and "C" or (v < 64 and ("L" .. (64 - v)) or ("R" .. (v - 64))) end
    text(label, x, ty, w / 2, px(16), "r", C.dim, 1)
    text(" " .. shown, x + w / 2, ty, w / 2, px(16), "l", bad and C.warn or C.text, 6)
    value_box(id .. ":box", ch, num, v, x, ty, w, px(16))
    return 2 * r + px(20)
end

-- パン：列の幅いっぱいの横長のバー。真ん中から左右に塗り、取っ手。真下に状態（C / L12 / R20）。
-- cur：曲の頭の値、now：今の位置の値（違えば、バーの上にマゼンタの ▾）。高さ h のあと、下に px(18) 使う
local function panbar(id, ch, cur, now, x, y, w, h, bad)
    local bx, bw = x + px(6), w - px(12)
    local v, hovered = control(id, ch, 10, cur, bx, y, bw, h, "h", math.max(bw, px(127)))
    local th = px(12)
    local ty = y + (h - th) / 2
    set(C.groove)
    rrect(bx, ty, bw, th, th / 2, true)
    local mid = bx + bw / 2
    local pos = bx + bw * v / 127
    set(bad and C.warn or C.accent2, hovered and 1 or 0.9)
    if math.abs(pos - mid) >= 1 then rrect(math.min(mid, pos), ty + px(2), math.abs(pos - mid), th - px(4), px(3), true) end
    set(C.faint)
    gfx.line(mid, ty - px(3), mid, ty + th + px(3))
    -- 取っ手
    local kw, kh = px(10), th + px(6)
    set({0.05, 0.05, 0.07})
    rrect(pos - kw / 2 - 1, ty - px(3) - 1, kw + 2, kh + 2, px(4), true)
    set(hovered and C.text or C.cap)
    rrect(pos - kw / 2, ty - px(3), kw, kh, px(4), true)
    -- 今の位置の値（曲の頭と違うとき）
    if now and now ~= v then
        local nx, ny = bx + bw * now / 127, ty - px(4)
        set({0.05, 0.05, 0.07})
        gfx.triangle(nx, ny + px(1), nx - px(6), ny - px(8), nx + px(6), ny - px(8))
        set(C.now)
        gfx.triangle(nx, ny, nx - px(5), ny - px(7), nx + px(5), ny - px(7))
    end
    -- 真下：状態
    local label = v == 64 and "C" or (v < 64 and ("L" .. (64 - v)) or ("R" .. (v - 64)))
    text(label, x, y + h, w, px(18), "c", bad and C.warn or C.text, 6)
    value_box(id .. ":box", ch, 10, v, x + w / 2 - px(24), y + h, px(48), px(18))
end

-- Expression のフェーダーとメーター
-- cur：つまみの値（曲の頭の Expression）、now：今の位置の Expression（右の ◂ で示す）
local function fader(id, ch, cur, now, x, y, w, h, bad, level)
    local mw = px(8)
    local gx = x + (w - mw - px(10)) / 2
    local gw = px(6)
    local v, hovered = control(id, ch, 11, cur, x, y, w - mw - px(6), h, "v", h, FADER_DOUBLE_CLICK)
    -- 目盛り
    set(C.faint)
    for _, m in ipairs({0, 32, 64, 96, 127}) do
        local yy = y + h - h * m / 127
        gfx.line(gx - px(10), yy, gx - px(4), yy)
        gfx.setfont(1)
        if w > px(70) then
            local s = tostring(m)
            local tw, th = gfx.measurestr(s)
            gfx.x, gfx.y = gx - px(12) - tw, yy - th / 2
            gfx.drawstr(s)
        end
    end
    -- 溝と塗り
    set(C.groove)
    rrect(gx, y, gw, h, px(3), true)
    set(bad and C.warn or C.accent, 0.9)
    local fy = y + h - h * v / 127
    rrect(gx, fy, gw, y + h - fy, px(3), true)
    -- つまみ
    local cw, ch_ = px(30), px(14)
    local cx, cy = gx + gw / 2 - cw / 2, fy - ch_ / 2
    gfx.gradrect(cx, cy, cw, ch_, 0.93, 0.94, 0.96, 1, 0, 0, 0, 0, -0.012, -0.012, -0.012, 0)
    set(hovered and C.accent or C.faint)
    gfx.roundrect(cx, cy, cw, ch_, px(3), 1)
    set({0.2, 0.22, 0.27})
    gfx.line(cx + px(4), cy + ch_ / 2, cx + cw - px(5), cy + ch_ / 2)
    -- 今の位置の Expression：溝の右に ◂（マゼンタ。白いつまみや青い塗りと重なっても見えるように、暗い縁を付ける）
    local ay = y + h - h * now / 127
    local ax = gx + gw + px(4)
    set({0.05, 0.05, 0.07})
    gfx.triangle(ax - px(1), ay, ax + px(8), ay - px(6), ax + px(8), ay + px(6))
    set(C.now)
    gfx.triangle(ax, ay, ax + px(7), ay - px(5), ax + px(7), ay + px(5))
    -- メーター（ベロシティ）
    local mx = x + w - mw - px(2)
    set(C.groove)
    rrect(mx, y, mw, h, px(3), true)
    if level > 0.001 then
        -- 下から緑・黄・赤
        for _, seg in ipairs({{0, 0.7, C.ok}, {0.7, 0.9, C.yellow}, {0.9, 1.0, C.red}}) do
            local a, b = seg[1], math.min(seg[2], level)
            if b > a then
                set(seg[3], 0.9)
                gfx.rect(mx, y + h - h * b, mw, h * (b - a), 1)
            end
        end
    end
    return v
end

-- ------------------------------------------------------------------ チャンネルの列
local function strip(ch, x, y, w, h, t)
    local tr = tracks.ch[ch]
    local vals = state.values(cache, ch, t)
    local name = tr and proj.track_name(tr) or ""
    local bad = tr and rules.check(ch, name, vals) or {}
    local hovered = inside(x, y, w, h)
    if hovered then hover_ch = ch end
    -- 背景
    gfx.gradrect(x, y, w, h, C.strip[1], C.strip[2], C.strip[3], 1, 0, 0, 0, 0, -0.00012, -0.00012, -0.0001, 0)
    set(hovered and C.line or C.strip2)
    gfx.roundrect(x, y, w, h, px(6), 1)
    local pad = px(6)
    local ix, iw = x + pad, w - 2 * pad
    local yy = y + pad
    -- 頭：番号と名前（チャンネルの色）
    local cr, cg, cb = hex(PALETTE[ch])
    local hh = px(40)
    set({cr, cg, cb}, tr and 1 or 0.35)
    rrect(ix, yy, iw, hh, px(5), true)
    local light = 0.2126 * cr + 0.7152 * cg + 0.0722 * cb > 0.6
    local tc = light and {0.08, 0.09, 0.11} or {1, 1, 1}
    text(tostring(ch), ix + px(4), yy, px(30), hh, "l", tc, 4)
    text(tr and track_name(ch) or "トラックなし", ix + px(30), yy, iw - px(34), hh, "l", tc, 3)
    yy = yy + hh + px(4)
    if not tr then
        set(C.faint)
        text("A" .. string.format("%02d", ch) .. " のトラックがありません", ix, yy, iw, px(20), "l", C.faint, 1)
        return
    end
    -- 音色
    text(program_name(ch, vals.program), ix, yy, iw, px(18), "l", C.dim, 1)
    yy = yy + px(20)
    -- EFX の割り当て
    local segw = (iw - px(30)) / 3
    text("EFX", ix, yy, px(28), px(22), "l", C.dim, 1)
    for k, lab in ipairs({"−", "A", "B"}) do
        local val = k - 1
        if button(lab, ix + px(30) + (k - 1) * segw, yy, segw - px(2), px(22), vals.efx == val and val ~= 0,
                  val == 2 and C.red or C.accent, 3) then
            commit_efx(ch, val)
        end
    end
    yy = yy + px(28)
    -- 送り（Rev・Cho・Del）：丸いつまみを ▲ の形に（上に Rev、下の左右に Cho と Del）
    -- つまみ・パンは曲の頭の値。今の位置の値が違えば、マゼンタの印で示す（Expression の ◂ と同じ）
    local function init(num) return state.initial(cache, ch, num, DEFAULTS[num]) end
    local kw = iw / 2
    local h1 = knob(ch .. ":91", ch, 91, "Rev", init(91), vals[91], ix + kw / 2, yy, kw, bad[91])
    local y2 = yy + h1 + px(2)
    local h2 = knob(ch .. ":93", ch, 93, "Cho", init(93), vals[93], ix, y2, kw, bad[93])
    knob(ch .. ":94", ch, 94, "Del", init(94), vals[94], ix + kw, y2, kw, bad[94])
    yy = y2 + h2 + px(10)
    -- パン
    panbar(ch .. ":10", ch, init(10), vals[10], ix, yy, iw, px(28), bad[10])
    yy = yy + px(28) + px(22)
    -- 下：ボリューム（固定）、Expression の数字
    local by = y + h - pad - px(20)
    local vol_ok = not bad[7]
    text(string.format("Vol %d", vals[7]) .. (vol_ok and "" or "  !"), ix, by, iw, px(20), "c", vol_ok and C.faint or C.warn, 1)
    by = by - px(26)
    -- フェーダー
    local fh = by - yy - px(26)
    if fh > px(40) then
        local init = state.initial(cache, ch, 11, 127)
        local ev = fader(ch .. ":11", ch, init, vals[11], ix, yy + px(8), iw, fh, bad[11], meter_level[ch])
        set(C.groove)
        rrect(ix + iw / 2 - px(26), by, px(52), px(22), px(4), true)
        text(tostring(ev), ix + iw / 2 - px(26), by, px(52), px(22), "c", bad[11] and C.warn or C.text, 5)
        value_box(ch .. ":11:box", ch, 11, ev, ix + iw / 2 - px(26), by, px(52), px(22))
    end
end

-- ------------------------------------------------------------------ EFXUI・ドラムを開く・閉じる
local SEP = DIR:match("[\\/]") or "/"
local PARENT = DIR:gsub("[^\\/]+[\\/]$", "")
local OTHERS = {
    efx = {name = "EFXUI", path = PARENT .. "EFXUI" .. SEP .. "xlaben_efx_controller.lua"},
    drum = {name = "ドラム（DrumReapermacro）", path = PARENT .. "DrumReapermacro" .. SEP .. "XLaben_Drum.lua"},
}

-- アクションに登録して（もうあれば同じ番号が返る）、実行する。もう一度実行すると閉じる
local function toggle_other(key)
    local o = OTHERS[key]
    local f = io.open(o.path, "rb")
    if not f then return say(o.name .. " が見つかりません：" .. o.path, 6) end
    f:close()
    o.cmd = o.cmd or r.AddRemoveReaScript(true, 0, o.path, true)
    if o.cmd and o.cmd > 0 then
        r.Main_OnCommand(o.cmd, 0)
    else
        say(o.name .. " を起動できませんでした", 6)
    end
end
local function other_on(key) return OTHERS[key].cmd ~= nil and r.GetToggleCommandState(OTHERS[key].cmd) == 1 end

-- ------------------------------------------------------------------ 上の帯
local function header(t, W)
    local hh = px(48)
    gfx.gradrect(0, 0, W, hh, C.panel[1], C.panel[2], C.panel[3], 1, 0, 0, 0, 0, -0.0004, -0.0004, -0.0003, 0)
    set(C.line)
    gfx.line(0, hh - 1, W, hh - 1)
    text("X-Laben Mixer", px(14), 0, px(160), hh, "l", C.text, 5)
    -- 再生・停止
    local bx, by, bs = px(170), px(10), px(28)
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
    if hov and not drag and gfx.mouse_cap & 1 == 0 and mouse_was & 1 == 1 then play.toggle(DIR) end
    -- 位置
    text(r.format_timestr_pos(t, "", 2), bx + bs + px(10), 0, px(90), hh, "l", C.text, 5)
    text(r.format_timestr_pos(t, "", 0), bx + bs + px(100), 0, px(90), hh, "l", C.dim, 1)
    -- EFX：EFXUI（となりのフォルダ）を開く・閉じる。開いている間は点灯
    local ex = bx + bs + px(200)
    if button("EFX", ex, px(10), px(60), px(28), other_on("efx"), C.accent2, 3) then toggle_other("efx") end
    if button("Drum", ex + px(68), px(10), px(66), px(28), other_on("drum"), C.accent2, 3) then toggle_other("drum") end
    -- 何を書くか
    text("つまみ：曲の頭の値　マゼンタの印：今の位置の値", W - px(560), 0, px(420), hh, "r", C.faint, 1)
    -- JSFX の様子
    local beat = live.heartbeat()
    if beat ~= beat_seen then beat_seen, beat_time = beat, r.time_precise() end
    local alive = r.time_precise() - beat_time < 0.6
    local jx = W - px(120)
    set(alive and C.ok or (playing() and C.red or C.faint))
    gfx.circle(jx, hh / 2, px(5), 1, 1)
    text(alive and "試聴 OK" or (playing() and "試聴が動いていない" or "試聴 待機中"), jx + px(10), 0, px(110), hh, "l", C.dim, 1)
    -- 右クリックのメニュー
    if inside(0, 0, W, gfx.h) and gfx.mouse_cap & 2 == 0 and mouse_was & 2 == 2 and not drag then
        gfx.x, gfx.y = gfx.mouse_x, gfx.mouse_y
        local dock = gfx.dock(-1)
        local sel = gfx.showmenu((dock & 1 == 1 and "!" or "") .. "ドッキング|大きさを戻す|プロジェクトを読み直す||"
                                 .. string.format("全部を送り直したときに、音源が落ち着くまで待つ時間（今 %d ms）…", play.settle_ms())
                                 .. "|次の再生では、全部を送り直す"
                                 .. "|" .. (play.diff_mode() and "!" or "") .. "（試験）違う CC だけ送る（オフなら毎回全部を送る）"
                                 .. string.format("|前から再生する長さ（今 %d ms、0 = 使わない）…", play.preroll_ms())
                                 .. "|" .. (sync.enabled() and "!" or "") .. "止まっている間に、カーソルの位置の状態を先に送っておく（Space をすぐ再生に）")
        if sel == 1 then gfx.dock(dock & 1 == 1 and 0 or 1)
        elseif sel == 2 then gfx.init("", 1180, 640)
        elseif sel == 3 then reload_all()
        elseif sel == 4 then
            local ok, v = r.GetUserInputs(TITLE, 1, "送り終わってから再生までのミリ秒（0〜2000）,extrawidth=60", tostring(play.settle_ms()))
            if ok and tonumber(v) then
                play.set_settle_ms(math.max(0, math.min(2000, tonumber(v))))
                say(string.format("全部を送り直したときは、再生前に %d ms 待ちます", play.settle_ms()))
            end
        elseif sel == 5 then
            play.force_full()
            say("次の再生では、全部を送り直します")
        elseif sel == 7 then
            local ok, v = r.GetUserInputs(TITLE, 1, "前から再生する長さ（ミリ秒。0 = 使わない）,extrawidth=60", tostring(play.preroll_ms()))
            if ok and tonumber(v) then
                play.set_preroll_ms(math.max(0, math.min(3000, tonumber(v))))
                say(play.preroll_ms() > 0 and string.format("押したらすぐ、%d ms 前から再生します（送る＋落ち着くより短いときは、その長さ）", play.preroll_ms())
                    or "前から再生しません（送り終わって落ち着いてから再生）")
            end
        elseif sel == 8 then
            sync.set_enabled(not sync.enabled())
            if sync.enabled() then sync.launch(DIR) end
            say(sync.enabled() and "止まっている間に先に送っておきます（XLaben_Sync）。準備ができていれば、Space ですぐ再生します"
                or "先に送っておくのをやめました（Space を押してから送ります）")
        elseif sel == 6 then
            play.set_diff_mode(not play.diff_mode())
            say(play.diff_mode() and "（試験）違う CC だけ送ります。できないときは、下の帯の右に理由が出ます"
                or "毎回全部を送ります")
        end
    end
    return hh
end

-- ------------------------------------------------------------------ 1コマ
local function save_window()
    local dock, x, y, w, h = gfx.dock(-1, 0, 0, 0, 0)
    r.SetExtState(EXT, "window", string.format("%d,%d,%d,%d,%d", w, h, dock, x, y), true)
end

-- REAPER のキーの割り当てから、このスクリプトのショートカットを読む（3 秒ごと）
local bindings, bindings_read = {}, -100
local own_names = keys.own_names(r)
local other_bindings = {}  -- ドラム・EFXUI に割り当てたショートカット（この画面で押されたら、そちらを実行）
local function read_bindings()
    bindings_read = r.time_precise()
    local f = io.open(r.GetResourcePath() .. "/reaper-kb.ini", "rb")
    if not f then return end
    local text = f:read("*a")
    f:close()
    bindings, other_bindings = keys.load(text, "mixer", own_names)
end

local last_save = 0
local function frame()
    local key = gfx.getchar()
    if key < 0 then return end
    win.track()
    local now = r.time_precise()
    local dt = now - last_frame
    last_frame = now
    S = math.max(0.75, math.min(1.6, gfx.h / 640))
    fonts()
    -- このミキサーに割り当てたショートカットが、ミキサーの画面の中で押されたら閉じる
    if now - bindings_read > 3 then read_bindings() end
    if key > 0 and keys.matches(bindings, key, gfx.mouse_cap) then
        gfx.quit()
        return
    end
    -- ドラム・EFXUI のショートカット：REAPER と同じく、そのスクリプトを実行（開く・前に出す・閉じる）
    if key > 0 and keys.run_other(r, other_bindings, key, gfx.mouse_cap) then key = 0 end
    if key == 32 then play.toggle(DIR)
    elseif key == 26 then r.Main_OnCommand(40029, 0); rescan(true)
    elseif key == 25 then r.Main_OnCommand(40030, 0); rescan(true) end

    rescan(false)
    local t = now_time()
    -- メーター
    for ch = 1, 16 do
        local vel, cnt = live.meter(ch)
        if meter_count[ch] ~= nil and cnt ~= meter_count[ch] then meter_level[ch] = math.max(meter_level[ch], vel / 127) end
        meter_count[ch] = cnt
        meter_level[ch] = meter_level[ch] * math.exp(-dt * 4)
    end

    local W, H = gfx.w, gfx.h
    set(C.bg)
    gfx.rect(0, 0, W, H, 1)
    hover_ch = nil
    local hh = header(t, W)
    local sh = px(26)
    -- 列を並べる（幅が足りなければ横にずらせる）
    local gap, pad = px(6), px(10)
    local area_w = W - 2 * pad
    local sw = math.max(px(96), (area_w - 15 * gap) / 16)
    local total = 16 * sw + 15 * gap
    local max_scroll = math.max(0, total - area_w)
    if inside(0, hh, W, H - hh - sh) and gfx.mouse_wheel ~= 0 and gfx.mouse_cap & 8 == 8 and not drag then
        scroll_x = scroll_x - gfx.mouse_wheel / 120 * sw
        gfx.mouse_wheel = 0
    end
    scroll_x = math.max(0, math.min(max_scroll, scroll_x))
    local top = hh + px(8)
    local sb = max_scroll > 0 and px(10) or 0
    local height = H - top - sh - px(8) - sb
    for i, ch in ipairs(ORDER) do
        local x = pad + (i - 1) * (sw + gap) - scroll_x
        if x + sw > 0 and x < W then strip(ch, x, top, sw, height, t) end
    end
    if max_scroll > 0 then
        local y = H - sh - sb - px(2)
        set(C.groove)
        rrect(pad, y, area_w, px(6), px(3), true)
        set(C.faint)
        rrect(pad + area_w * scroll_x / total, y, area_w * area_w / total, px(6), px(3), true)
    end
    update_drag()
    update_wheel()
    gfx.mouse_wheel = 0
    -- 下の帯
    set(C.panel)
    gfx.rect(0, H - sh, W, sh, 1)
    local msg = status
    if now > status_until then
        msg = ""
        if hover_ch then
            local tr = tracks.ch[hover_ch]
            if tr then
                local bad = rules.check(hover_ch, proj.track_name(tr), state.values(cache, hover_ch, t))
                local list = {}
                for _, why in pairs(bad) do list[#list + 1] = why end
                msg = #list > 0 and ("ch" .. hover_ch .. "：" .. table.concat(list, "、")) or ("ch" .. hover_ch .. "：決まりどおり")
            end
        end
    end
    text(msg, px(12), H - sh, W * 0.6, sh, "l", (msg ~= "" and msg:find("：") and msg:find("決まりどおり") == nil and hover_ch and now > status_until) and C.warn or C.dim, 1)
    local info = r.GetExtState(EXT, "last_play")
    text(info, W * 0.6, H - sh, W * 0.4 - px(12), sh, "r", C.faint, 1)
    mouse_was = gfx.mouse_cap
    if now - last_save > 2 then save_window(); last_save = now end
    gfx.update()
    r.defer(frame)
end

local function cleanup()
    win.finish(EXT)  -- 見えていたかを残し、ツールバーのボタンを消灯
    live.hold_cc(nil)
    save_window()
end

local function main()
    -- もう一度実行されたら：見えていれば閉じる、ドッキングの裏に隠れていれば前に出す（lib/xl_window.lua）
    if not win.start(EXT) then return end
    rescan(true)
    if tracks.xlaben then
        local ok, err = proj.ensure_jsfx(tracks.xlaben, DIR)
        if not ok then say(err, 10) end
    else
        say("「X-Laben」トラックが見つからないので、試聴はできません", 10)
    end
    live.hold_cc(nil)
    sync.launch(DIR)  -- 止まっている間に先に送っておく（XLaben_Sync。もう動いていれば何もしない）
    local w, h, dock, x, y = (r.GetExtState(EXT, "window")):match("(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+)")
    gfx.ext_retina = 1
    gfx.init(TITLE, tonumber(w) or 1180, tonumber(h) or 640, tonumber(dock) or 0, tonumber(x) or 120, tonumber(y) or 120)
    r.atexit(cleanup)
    frame()
end

main()
