-- @description X-Laben オートストラマー（選んだノートを、和音ごとにストラムする：ずらし・強さ・ノートオフ）
-- @version 3.0.0
--
-- 使い方：MIDI エディターでノートを選び、値を決めて Apply（A）。和音（重なっているノート）ごとに、弾く順に Offset ずつずらす。
--   値を変えただけではノートは変えない（プレビューは無い）。変えるのは Apply・Enter・プリセット（数字キー・ボタン）のときだけ。
--   1 回の Apply は Undo 1 回：Ctrl+Z で戻す、Ctrl+Y（Ctrl+Shift+Z）でやり直す（REAPER の元に戻す・やり直す）。
--   何度 Apply しても、選んだときの位置から計算し直す（ずれが重ならない）。
--   ノートを選ぶと、今の弾き方（Offset・Min / Max・Up / Down・Move Note Off）を読んで値に出す。
-- つまみ（横のバー）：ドラッグ（Shift で細かく）、ダブルクリックで数字を入れる、Ctrl+クリックで既定の値。ホイールでは変えない。
-- キー：Z/X Offset、D/F Min、C/V Max、B/N Global、G/H Note Off（Brushing のときは長さ）、E Up/Down、Q Move Note Off、
--   W Brushing、T Ruler Select、A Apply、Enter Apply して閉じる、Esc 閉じる、Space 再生・停止、
--   1〜9・0 プリセットを読み込んで Apply（S を押してから数字で保存）。プリセットのボタン：押すと Apply、空なら保存、Shift+クリックで消す。
--   数字キーと S は、MIDI エディターで作業しているときも効く（この窓が開いている間。js_ReaScriptAPI が要る）。
-- Ruler Select：編集カーソルを動かすと、そこで鳴っているノートだけを選ぶ（和音を 1 つずつ選んでいける）。
-- ドッキング：右クリック。もう一度実行すると、見えていれば閉じる（ほかのツールと同じ）。ほかのツールのショートカットもこの画面で効く。
-- プリセットは XLabenTools\user\strum_presets.json（更新で消えない）。前の AutoStrummer_presets.lua は最初に開いたときに移す。

local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local SEP = DIR:match("[\\/]") or "/"
local MIXER_DIR = DIR:gsub("[^\\/]+[\\/]$", "") .. "XLabenMixer" .. SEP
local function need(path)
    local f = io.open(path, "rb")
    if not f then
        r.ShowMessageBox("見つかりません：" .. path .. "\nXLabenMixer を、このフォルダのとなりに置いてください。", "X-Laben Strum", 0)
        return nil
    end
    f:close()
    return dofile(path)
end
local keys = need(MIXER_DIR .. "lib/xl_keys.lua")
if not keys then return end
local play = dofile(MIXER_DIR .. "lib/xl_play.lua")
local win = dofile(MIXER_DIR .. "lib/xl_window.lua")
local core = dofile(DIR .. "lib/strum_core.lua")
local store = dofile(DIR .. "lib/strum_store.lua")
local tk = dofile(DIR .. "lib/strum_take.lua")

local TITLE = "X-Laben Strum"
local EXT = "XLabenStrum"

-- ------------------------------------------------------------------ 色（前のオートストラマーのまま）
local C = {
    bg = {0.08, 0.08, 0.08}, band = {0.15, 0.15, 0.15}, button = {0.2, 0.2, 0.2}, seg = {0.18, 0.18, 0.18},
    track = {0.15, 0.15, 0.15}, knob = {0.8, 0.8, 0.8}, text = {1, 1, 1}, hint = {0.85, 0.85, 0.85},
    red = {0.85, 0.25, 0.25}, green = {0.2, 0.75, 0.2}, flash = {0.10, 0.50, 0.10}, save = {0.8, 0.0, 0.0},
}
local function set(c, a) gfx.set(c[1], c[2], c[3], a or 1) end

local S = 1
local function px(v) return math.floor(v * S + 0.5) end
local function fonts()
    gfx.setfont(1, "Meiryo UI", px(12))
    gfx.setfont(2, "Meiryo UI", px(14))
    gfx.setfont(3, "Meiryo UI", px(14), string.byte("b"))
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
    return ""
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

-- ------------------------------------------------------------------ プリセット（user\strum_presets.json）
local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end
local function write_file(path, s)
    r.RecursiveCreateDirectory(path:match("^(.*)[\\/]"), 0)
    local f = io.open(path, "wb")
    if not f then return false end
    f:write(s)
    f:close()
    return true
end
-- 配った形（XLabenTools\REAPER\AutoStrummer）なら XLabenTools\user、開発のフォルダなら、そのフォルダの user
local function user_dir()
    local root = DIR:gsub("[^\\/]+[\\/]$", ""):gsub("[^\\/]+[\\/]$", "")
    if r.file_exists(root .. "MANIFEST.txt") then return root .. "user" .. SEP end
    return DIR .. "user" .. SEP
end
local PRESETS = XLABEN_STRUM_PRESETS or (user_dir() .. "strum_presets.json")   -- テストでは別の場所
local OLD_DIR = XLABEN_STRUM_OLD_DIR or DIR                                  -- 前の形のファイルがある所
-- 前の形のファイル：このフォルダに無ければ、更新が片付けた先（XLabenTools\old_macros\<日付>\AutoStrummer）の新しいほうから
local function old_text(name)
    local s = read_file(OLD_DIR .. name)
    if s then return s end
    local om = (XLABEN_STRUM_OLD_MACROS or user_dir():gsub("user[\\/]$", "") .. "old_macros" .. SEP)
    local best
    local i = 0
    while r.EnumerateSubdirectories do
        local d = r.EnumerateSubdirectories(om, i)
        if not d then break end
        if (not best or d > best) and r.file_exists(om .. d .. SEP .. "AutoStrummer" .. SEP .. name) then best = d end
        i = i + 1
    end
    return best and read_file(om .. best .. SEP .. "AutoStrummer" .. SEP .. name)
end
local presets = {}
local function load_presets()
    local s = read_file(PRESETS)
    if s then presets = store.from_json(s) return end
    local old = store.read_old(old_text("AutoStrummer_presets.lua"))   -- 前の形から移す
    presets = old or {}
    if old then write_file(PRESETS, store.to_json(presets)) end
end
local function save_presets()
    if not write_file(PRESETS, store.to_json(presets)) then return false end
    return true
end

-- ------------------------------------------------------------------ 状態
local params = core.normalize({})
local take, base, current = nil, {}, {}
local seen_sig, applied_sig = nil, nil
local ruler = false
local save_mode = false
local status, status_until = "", 0
local flash_until = 0
local closing = false
local drag = nil          -- {key, x, w, lo, hi, fine, x0, v0}
local mouse_was, last_click_t, last_click_id = 0, 0, nil
local ruler_seen, ruler_moved_at = nil, 0
XLABEN_STRUM_LAYOUT = {}

local function say(s, secs) status, status_until = s, r.time_precise() + (secs or 4) end

do  -- Ruler Select は ExtState に（前の AutoStrummer_settings.lua に On があれば、それを引き継ぐ）
    local v = r.GetExtState(EXT, "ruler")
    if v == "" then
        local old = old_text("AutoStrummer_settings.lua") or ""
        v = old:match("rulerSelectMode%s*=%s*true") and "1" or "0"
        r.SetExtState(EXT, "ruler", v, true)
    end
    ruler = v == "1"
end

-- 選んでいるノートを読む。選び直した・元に戻した（自分の Apply 以外で変わった）ら、もとのノートにして弾き方を読む
local function refresh()
    take = tk.active_take()
    current = tk.selected_notes(take)
    local sig = tk.signature(take, current)
    if sig ~= seen_sig then
        seen_sig = sig
        if sig ~= applied_sig then
            base = current
            if #current > 0 then params = core.infer(current, params) end
        end
    end
end

local function apply(label)
    refresh()
    if not take then say("MIDI のテイクがありません（MIDI エディターを開くか、MIDI アイテムを選ぶ）", 5) return false end
    if #current == 0 then say("ノートを選んでください", 4) return false end
    local p = core.normalize(params)
    params = p
    local ok = tk.apply(take, base, current, core.plan(base, p), label)
    if not ok then   -- 選んでいるノートが、もとのノートと違う：今のノートから計算する
        base = current
        ok = tk.apply(take, base, current, core.plan(base, p), label)
    end
    if not ok then say("書けませんでした", 4) return false end
    current = tk.selected_notes(take)
    applied_sig = tk.signature(take, current)
    seen_sig = applied_sig
    flash_until = r.time_precise() + 0.12
    local chords = #core.chords(base)
    say(string.format("Apply：%d 音（和音 %d）。Ctrl+Z で戻す", #base, chords), 3)
    return true
end

local function load_slot(i)
    local p = presets[i]
    if not p then return false end
    local q = {}
    for k, v in pairs(params) do q[k] = v end
    for k, v in pairs(p) do q[k] = v end
    params = core.normalize(q)
    return true
end

local function save_slot(i)
    local p = {}
    for _, k in ipairs(store.FIELDS) do p[k] = params[k] end
    presets[i] = p
    if save_presets() then say(string.format("プリセット %d に保存しました", i % 10), 3)
    else say("プリセットを保存できません：" .. PRESETS, 6) end
end

local function use_slot(i)
    if not presets[i] then say(string.format("プリセット %d は空です（S を押してから %d で保存）", i % 10, i % 10), 4) return end
    load_slot(i)
    apply(string.format("ストラム（プリセット %d）", i % 10))
end

-- ------------------------------------------------------------------ 描く道具（色は前のまま、形はほかのツールと同じ）
local function button(label, x, y, w, h, color)
    local hov = inside(x, y, w, h)
    set(color or C.button, color and 0.85 or 1)
    gfx.rect(x, y, w, h, 1)
    if hov then set(C.text, 0.15) gfx.rect(x, y, w, h, 1) end
    set(C.text)
    gfx.rect(x, y, w, h, 0)
    text(label, x, y, w, h, "c", C.text, 2)
    return hov
end

-- 2 つから選ぶ（左右）。選んだ番号（1 か 2）
local function segment(x, y, w, h, labels, selected, colors, click)
    local sw = (w - 2) // 2
    for i = 1, 2 do
        local sx = x + (i - 1) * (sw + 2)
        local on = selected == i
        set(on and colors[i] or C.seg, on and 0.85 or 1)
        gfx.rect(sx, y, sw, h, 1)
        set(C.text)
        gfx.rect(sx, y, sw, h, 0)
        if inside(sx, y, sw, h) then set(C.text, 0.08) gfx.rect(sx, y, sw, h, 1) end
        text(labels[i], sx, y, sw, h, "c", C.text, 2)
        if click and inside(sx, y, sw, h) then selected = i end
    end
    return selected
end

-- 横のバー：key の値。ラベルと値は上の行に
local function slider(key, label, x, y, w, click, press)
    local rg = core.RANGES[key]
    local lo, hi = rg[1], rg[2]
    local v = params[key]
    local lh, bh = px(18), px(20)
    text(label, x, y, w - px(60), lh, "l", C.text, 2)
    text(tostring(v), x + w - px(60), y, px(60), lh, "r", C.text, 3)
    local by = y + lh + px(2)
    set(C.track)
    gfx.rect(x, by + bh // 2 - 2, w, 4, 1)
    local t = (v - lo) / (hi - lo)
    set(C.knob)
    gfx.rect(math.floor(x + t * w) - px(5), by, px(10), bh, 1)
    XLABEN_STRUM_LAYOUT[key] = {x, by, w, bh}
    local hov = inside(x - px(5), by, w + px(10), bh)
    if press and hov then
        local now = r.time_precise()
        if gfx.mouse_cap & 4 == 4 then
            params[key] = core.DEFAULTS[key]                      -- Ctrl+クリック：既定の値
        elseif last_click_id == key and now - last_click_t < 0.35 then
            local ok, s = r.GetUserInputs(label, 1, string.format("%s（%d〜%d）", label, lo, hi), tostring(v))   -- ダブルクリック：数字を入れる
            if ok and tonumber(s) then params[key] = tonumber(s) end
            last_click_id = nil
        else
            drag = {key = key, x = x, w = w, lo = lo, hi = hi, x0 = gfx.mouse_x, v0 = v, fine = false}
            last_click_id, last_click_t = key, now
        end
        params = core.normalize(params)
    end
    return y + lh + bh + px(10)
end

local function update_drag()
    if not drag then return end
    if gfx.mouse_cap & 1 == 0 then drag = nil return end
    local d = drag
    if gfx.mouse_cap & 8 == 8 then   -- Shift：細かく
        if not d.fine then d.fine, d.x0, d.v0 = true, gfx.mouse_x, params[d.key] end
        params[d.key] = d.v0 + (gfx.mouse_x - d.x0) / d.w * (d.hi - d.lo) * 0.1
    else
        if d.fine then d.fine = false end
        params[d.key] = d.lo + math.max(0, math.min(1, (gfx.mouse_x - d.x) / d.w)) * (d.hi - d.lo)
    end
    params = core.normalize(params)
    if d.key == "minVel" and params.minVel > params.maxVel then params.minVel = params.maxVel end
end

-- ------------------------------------------------------------------ 画面
local function draw(click, press)
    local W, H = gfx.w, gfx.h
    local L = {}
    XLABEN_STRUM_LAYOUT = L
    local flashing = r.time_precise() < flash_until
    set(flashing and C.flash or (save_mode and C.save or C.bg))
    gfx.rect(0, 0, W, H, 1)
    -- 上の帯：対象と、選んでいるノート
    local hh = px(30)
    set(C.band)
    gfx.rect(0, 0, W, hh, 1)
    text("Strum", px(12), 0, px(70), hh, "l", C.text, 3)
    local info
    if not take then info = "MIDI のテイクがありません"
    elseif #current == 0 then info = "ノートを選んでください"
    else info = string.format("選んでいるノート %d 音（和音 %d）", #current, #core.chords(current)) end
    text(info, px(84), 0, W - px(96), hh, "l", C.hint, 2)
    if save_mode then text("保存：数字キーかボタンで保存する番号を選ぶ（Esc でやめる）", px(84), 0, W - px(96), hh, "r", C.text, 3) end

    local pad = px(16)
    local x, y, w = pad, hh + px(10), W - 2 * pad
    y = slider("offsetMs", "Offset / ms（Z / X）", x, y, w, click, press)
    local half = (w - px(20)) // 2
    slider("minVel", "Min Velocity（D / F）", x, y, half, click, press)
    y = slider("maxVel", "Max Velocity（C / V）", x + half + px(20), y, half, click, press)
    y = slider("globalShift", "Global Vel Shift（B / N）", x, y, w, click, press)
    if params.brushing then
        y = slider("brushLenMs", "Note Length / ms（G / H）", x, y, w, click, press)
    else
        y = slider("offShiftMs", "Note Off Shift / ms（G / H）", x, y, w, click, press)
    end
    -- 切り替え
    local rows = {
        {"Direction（E）", {"Up", "Down"}, params.upStrum and 1 or 2, function(i) params.upStrum = i == 1 end, "direction"},
        {"Move Note Off（Q）", {"Off", "On"}, params.moveNoteOff and 2 or 1, function(i) params.moveNoteOff = i == 2 end, "move_off"},
        {"Brushing（W）", {"Off", "On"}, params.brushing and 2 or 1, function(i) params.brushing = i == 2 end, "brushing"},
        {"Ruler Select（T）", {"Off", "On"}, ruler and 2 or 1, function(i)
            ruler = i == 2
            r.SetExtState(EXT, "ruler", ruler and "1" or "0", true)
            ruler_seen = nil
        end, "ruler"},
    }
    local seg_x, seg_w, rh = x + px(170), px(180), px(26)
    for _, row in ipairs(rows) do
        text(row[1], x, y, px(165), rh, "l", C.text, 2)
        local before = row[3]
        local after = segment(seg_x, y, seg_w, rh, row[2], before, {C.red, C.green}, click)
        L[row[5]] = {seg_x, y, seg_w, rh}
        if after ~= before then row[4](after) end
        y = y + rh + px(6)
    end
    -- プリセット（1〜9、0）
    y = y + px(6)
    text("プリセット：押すと Apply（空なら保存）／ S のあと番号で保存／ Shift+クリックで消す", x, y, w, px(18), "l", C.hint, 1)
    y = y + px(20)
    local pw = math.min(px(40), (w - 9 * px(6)) // 10)
    L.preset = {}
    for i = 1, 10 do
        local px_ = x + (i - 1) * (pw + px(6))
        local p = presets[i]
        local color = p and (p.upStrum and C.red or C.green) or nil
        L.preset[i] = {px_, y, pw, px(26)}
        if button(i < 10 and tostring(i) or "0", px_, y, pw, px(26), color) and click then
            if gfx.mouse_cap & 8 == 8 then
                if p and r.ShowMessageBox(string.format("プリセット %d を消しますか？", i % 10), TITLE, 4) == 6 then
                    presets[i] = nil
                    save_presets()
                end
            elseif save_mode or not p then
                save_slot(i)
                save_mode = false
            else
                use_slot(i)
            end
        end
    end
    -- 下のボタン
    local by = H - px(30) - px(34)
    local bw = (w - 2 * px(10)) // 3
    L.apply = {x, by, bw, px(28)}
    L.apply_close = {x + bw + px(10), by, bw, px(28)}
    L.close = {x + 2 * (bw + px(10)), by, bw, px(28)}
    if button("Apply（A）", x, by, bw, px(28)) and click then apply() end
    if button("Apply＋閉じる（Enter）", x + bw + px(10), by, bw, px(28)) and click then
        if apply() then closing = true end
    end
    if button("閉じる（Esc）", x + 2 * (bw + px(10)), by, bw, px(28)) and click then closing = true end
    -- 下の行：知らせ
    local sh = px(26)
    set(C.band)
    gfx.rect(0, H - sh, W, sh, 1)
    local msg = r.time_precise() < status_until and status or "Ctrl+Z で元に戻す・Ctrl+Y でやり直す（REAPER と同じ）"
    text(msg, px(12), H - sh, W - px(24), sh, "l", C.hint, 1)
end

-- ------------------------------------------------------------------ キー
local NUDGE = {z = {"offsetMs", -1}, x = {"offsetMs", 1}, d = {"minVel", -1}, f = {"minVel", 1},
               c = {"maxVel", -1}, v = {"maxVel", 1}, b = {"globalShift", -1}, n = {"globalShift", 1}}
local function on_key(c, cap)
    local ch = (c >= 65 and c <= 90) and string.char(c + 32) or (c >= 97 and c <= 122) and string.char(c) or nil
    if c == 27 then
        if save_mode then save_mode = false else closing = true end
    elseif c == 13 then if apply() then closing = true end
    elseif c == 32 then play.toggle(MIXER_DIR)
    elseif c == 26 and cap & 8 == 8 then r.Main_OnCommand(40030, 0)   -- Ctrl+Shift+Z：やり直す
    elseif c == 26 then r.Main_OnCommand(40029, 0)                   -- Ctrl+Z：元に戻す
    elseif c == 25 then r.Main_OnCommand(40030, 0)                   -- Ctrl+Y：やり直す
    elseif c >= 48 and c <= 57 then
        local i = c == 48 and 10 or c - 48
        if save_mode then save_slot(i) save_mode = false else use_slot(i) end
    elseif ch and NUDGE[ch] then
        local k = NUDGE[ch]
        params[k[1]] = params[k[1]] + k[2]
        params = core.normalize(params)
    elseif ch == "g" or ch == "h" then
        local k = params.brushing and "brushLenMs" or "offShiftMs"
        params[k] = params[k] + (ch == "g" and -1 or 1)
        params = core.normalize(params)
    elseif ch == "e" then params.upStrum = not params.upStrum
    elseif ch == "q" then params.moveNoteOff = not params.moveNoteOff
    elseif ch == "w" then params.brushing = not params.brushing
    elseif ch == "t" then
        ruler = not ruler
        r.SetExtState(EXT, "ruler", ruler and "1" or "0", true)
        ruler_seen = nil
    elseif ch == "a" then apply()
    elseif ch == "s" then save_mode = not save_mode
    end
end

-- プリセットのキーは、MIDI エディターで作業しているとき（この窓がアクティブでないとき）も効かせる（js_ReaScriptAPI が要る）。
--   この窓が開いていて、フォーカスが MIDI エディターにある間だけ、数字キー（上の段とテンキー）と S を REAPER より先に受け取る。
--   Ctrl・Shift・Alt を押している間は受け取らない（MIDI エディターのショートカットのまま）。
local HOT = {{0x53, 115}}   -- {仮想キー, 文字}。S を先に（S → 数字を同じフレームで押しても、保存になる）
for i = 0, 9 do HOT[#HOT + 1] = {0x30 + i, 48 + i} HOT[#HOT + 1] = {0x60 + i, 48 + i} end
local hot_on, hot_t = false, 0
local function hot_wanted()
    if closing or not (r.JS_VKeys_Intercept and r.JS_VKeys_GetDown and r.JS_Window_GetFocus) then return false end
    local me = r.MIDIEditor_GetActive()
    local focus = r.JS_Window_GetFocus()
    if not me or not focus then return false end
    if focus ~= me and not (r.JS_Window_IsChild and r.JS_Window_IsChild(me, focus)) then return false end
    if r.JS_Mouse_GetState and r.JS_Mouse_GetState(4 | 8 | 16) ~= 0 then return false end
    return true
end
local function hot_set(on)
    if on == hot_on then return end
    hot_on = on
    for _, k in ipairs(HOT) do r.JS_VKeys_Intercept(k[1], on and 1 or -1) end
end
local function hot_keys()
    local want = hot_wanted()
    if want and not hot_on then hot_t = r.time_precise() end   -- 受け取り始める前に押したキーは数えない
    hot_set(want)
    if not hot_on then return end
    local t = r.time_precise()
    local down = r.JS_VKeys_GetDown(hot_t) or ""
    hot_t = t
    for _, k in ipairs(HOT) do
        local b = down:byte(k[1])
        if b and b ~= 0 then on_key(k[2], 0) end
    end
end

-- Ruler Select：編集カーソルが動いて 20 ms 止まったら、そこで鳴っているノートを選ぶ
local function update_ruler()
    if not ruler or not take then return end
    local t = r.GetCursorPosition()
    local now = r.time_precise()
    if t ~= ruler_seen then ruler_seen, ruler_moved_at = t, now return end
    if ruler_moved_at and now - ruler_moved_at >= 0.02 then
        tk.select_at(take, t)
        ruler_moved_at = nil
    end
end

-- ------------------------------------------------------------------ 回す
local bindings, other_bindings, bindings_read = {}, {}, -100
local own_names = keys.own_names(r)
local function read_bindings()
    bindings_read = r.time_precise()
    local s = read_file(r.GetResourcePath() .. "/reaper-kb.ini")
    if s then bindings, other_bindings = keys.load(s, "strum", own_names) end
end

local finished = false
local function finish()
    if finished then return end
    finished = true
    hot_set(false)   -- 受け取っていたキーを REAPER に戻す
    win.finish(EXT)
    local dock, x, y, w, h = gfx.dock(-1, 0, 0, 0, 0)
    r.SetExtState(EXT, "window", string.format("%d,%d,%d,%d,%d", w, h, dock, x, y), true)
end

local function loop()
    local c = gfx.getchar()
    if c < 0 or closing then finish() gfx.quit() return end
    win.track()
    local now = r.time_precise()
    S = math.max(0.75, math.min(1.6, gfx.h / 500))
    fonts()
    if now - bindings_read > 3 then read_bindings() end
    refresh()
    update_ruler()
    hot_keys()
    while c > 0 do
        if keys.matches(bindings, c, gfx.mouse_cap) then closing = true break end   -- 自分のショートカット：閉じる
        if not keys.run_other(r, other_bindings, c, gfx.mouse_cap) then on_key(c, gfx.mouse_cap) end
        c = gfx.getchar()
    end
    local cap = gfx.mouse_cap
    local press = cap & 1 == 1 and mouse_was & 1 == 0
    local click = cap & 1 == 0 and mouse_was & 1 == 1
    if cap & 2 == 0 and mouse_was & 2 == 2 and inside(0, 0, gfx.w, gfx.h) then   -- 右クリック：ドッキング
        local dock = gfx.dock(-1)
        gfx.x, gfx.y = gfx.mouse_x, gfx.mouse_y
        if gfx.showmenu((dock & 1 == 1 and "!" or "") .. "ドッキング") == 1 then gfx.dock(dock & 1 == 1 and 0 or 1) end
    end
    mouse_was = cap
    gfx.mouse_wheel = 0   -- ホイールでは値を変えない
    update_drag()
    draw(click and not drag, press)
    gfx.update()
    r.defer(loop)
end

if not win.start(EXT) then return end
load_presets()
local w, h, dock, x, y = (r.GetExtState(EXT, "window")):match("(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+)")
gfx.init(TITLE, tonumber(w) or 540, tonumber(h) or 500, tonumber(dock) or 0, tonumber(x) or 200, tonumber(y) or 200)
fonts()
r.atexit(finish)
loop()
