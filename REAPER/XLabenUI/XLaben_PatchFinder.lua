-- @description X-Laben Patch Finder（音色を選ぶ・トラックに入れる。Cakewalk の Patch Finder と同じ並び）
-- @version 1.3.0
--
-- 上：Patch Finder（画像と同じ並び。窓の大きさは決まっている）
--   B.MSB（バンク）のタブ、プログラムの表（21 行 × 4 列が見える。084 から先は下のスクロールバーで横に送る）、
--   Variations（選んだプログラムのバンク違い）、Drum、Include B.LSB Variations、B.LSB（マップ）、CwBank#（MSB × 128 + LSB）、
--   音色の検索（言葉を入れると第一候補を左上の表に出す。下の ◀ ▶ で候補を送る・戻す、そのとき鳴らす）、OK
-- 下：トラックに入れるボタン（Ch1〜16。今の音色と MSB / LSB / PC が出る）。押すと、選んでいる音色をそのトラックの
--   セットアップ（最初のノートより前）の CC0・CC32・PC に書き、音源にも送る（Ctrl+Z で戻る）。
-- 音色をクリック：選んでいるトラック（窓の題名に出る）のチャンネルで、そのトラックの設定（音量・パン・送りなど）のまま鳴らす。
--   X-Laben トラックの JSFX から「試聴だけ」で送る（JSFX は覚えない）ので、何度鳴らしても、再生のスクリプトや Sync が
--   「音源の中が変わった」と見てリセットから全部を送り直すことはない。Drum を変えてキットを鳴らすときは、そのパートを一時的にドラムにする。
--   別のトラックを選ぶ・閉じると、そのチャンネルを元に戻す（今の位置の音色、ドラムのパート、ドラムの音ごとの設定）。
-- OK（Enter）：選んでいるトラック（REAPER で選んでいる A01〜A16。窓の題名に出る）に書いて閉じる。
--   選んでいなければ前のトラック、それも無ければいちばん若い番号。下の Ch のボタンを押すと、そのトラックに書く。
-- 窓の大きさは決まっている（引き延ばしても戻す。ドッキングしたときは除く）。右クリック：ドッキングする・やめる。
-- キー：↑↓ プログラム、←→ 列、Enter OK、Esc 閉じる、Space 再生・停止（XLaben_Play と同じ）
-- 送るのは、ミキサー（XLabenMixer）の部品と JSFX。XLabenMixer をこのフォルダのとなりに置く。

local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local SEP = DIR:match("[\\/]") or "/"
local MIXER_DIR = DIR:gsub("[^\\/]+[\\/]$", "") .. "XLabenMixer" .. SEP
local function need(path)
    local f = io.open(path, "rb")
    if not f then
        r.ShowMessageBox("見つかりません：" .. path .. "\nXLabenMixer を、このフォルダのとなりに置いてください。", "X-Laben Patch Finder", 0)
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
local win = dofile(MIXER_DIR .. "lib/xl_window.lua")
local sysex = dofile(MIXER_DIR .. "lib/xl_sysex.lua")
local pdb = dofile(DIR .. "lib/pf_db.lua")
local setup = dofile(DIR .. "lib/pf_setup.lua")
local ok_db, TONES = pcall(dofile, DIR .. "data/tones_db.lua")
if not ok_db or not TONES then
    r.ShowMessageBox("音色の表が読めません：" .. DIR .. "data/tones_db.lua", "X-Laben Patch Finder", 0)
    return
end
local db = pdb.new(TONES.tones)
local function read_text(path)
    local f = io.open(path, "rb")
    if not f then return "" end
    local s = f:read("*a")
    f:close()
    return s
end

local TITLE = "Patch Finder"
local EXT = "XLabenPatch"
local AUDITION_S = 0.5      -- 鳴らす長さ
local NOTE = {normal = 60, bass = 48, drum = 36}
local W, H = 790, 520       -- 窓の大きさ（決まっている）
local ROWS, COL_W = 21, 141 -- プログラムの表：1 列の行数、列の幅（4 列が見える）

-- ------------------------------------------------------------------ 色・描く道具（ミキサーと同じ）
local function hex(h, a)
    h = h:gsub("#", "")
    return tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, a or 1
end
local C = {
    bg = {hex("#14161b")}, panel = {hex("#1c1f26")}, strip = {hex("#232730")}, strip2 = {hex("#1e2129")},
    line = {hex("#2e333d")}, text = {hex("#e7e9ee")}, dim = {hex("#8d93a3")}, faint = {hex("#5b6170")},
    accent = {hex("#4ea1ff")}, warn = {hex("#ffad33")}, ok = {hex("#3dd17f")}, groove = {hex("#0f1115")},
    drum = {hex("#3dd17f")},
}
local function set(c, a) gfx.set(c[1], c[2], c[3], a or c[4] or 1) end
local function fonts()
    gfx.setfont(1, "Meiryo UI", 11)
    gfx.setfont(2, "Meiryo UI", 12)
    gfx.setfont(3, "Meiryo UI", 12, string.byte("b"))
end
local function fill(c, x, y, w, h, a) set(c, a) gfx.rect(x, y, w, h, 1) end
local function frame_rect(c, x, y, w, h) set(c) gfx.rect(x, y, w, h, 0) end
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

-- ------------------------------------------------------------------ 状態
local tracks = {ch = {}}
local rhythm = {[10] = true}
local target = nil            -- {ch, tr, name, tone = プロジェクトの音色 {msb, lsb, pc} か nil}
-- 試聴：ch（鳴らしたチャンネル）、active（試聴で音源を変えた）、rhythm（今のドラムのパートの値 0 / 1 / 2）
local preview = {ch = nil, active = false}
local sel = {msb = 0, lsb = 3, pc = 0, drum = false}
local include_all = false
local msb_scroll, col_scroll, var_scroll = 0, 0, 0
local search, search_on = "", false   -- 検索の言葉、入力中か
local found, found_i = {}, 0  -- 検索の候補と、今出している番号
local last_resize = 0
local status, status_until = "", 0
local last_change, last_sel_tr = -1, nil
local audition_off = nil      -- {ch, note, at}
local mouse_was = 0
local drag = nil              -- スクロールバーのつまみをつまんでいる：{x0, scroll0}
local closing = false
local ch_tones = {}           -- [ch] = {name, msb, lsb, pc} / false（セットアップに無い）/ nil（トラックが無い）
local ch_flash = {}           -- [ch] = 光らせる終わりの時刻
local shown_title = nil
XLABEN_PF_LAYOUT = {}         -- 描いた所（テストで使う）

local function say(s, secs) status, status_until = s, r.time_precise() + (secs or 4) end
local function tone_of(s) return pdb.get(db, s.drum, s.lsb, s.msb, s.pc) end
local COLS = (128 + ROWS - 1) // ROWS   -- 7
local COLS_VISIBLE = 4

-- ------------------------------------------------------------------ 音源へ送る（JSFX）
local function quiet(msg, delay) live.send(msg, delay or 1, true) end   -- 試聴だけ（JSFX は覚えない）

-- プロジェクトの、ch の Use for Rhythm Part（0 = ふつう、1・2 = ドラム）。リセットのあとは ch10 だけが 1
local function rhythm_value(ch)
    local v = ch == 10 and 1 or 0
    if not tracks.sysex then return v end
    for _, m in ipairs(proj.messages_until(tracks.sysex, math.huge, "sysex")) do
        if sysex.is_reset(m) then
            v = ch == 10 and 1 or 0
        else
            local x = sysex.parse(m)
            if x and x.a1 == 0x40 and x.a2 == proj.RHYTHM_BLOCK[ch] and x.a3 == 0x15 and x.data[1] then v = x.data[1] end
        end
    end
    return v
end

local function note_off()
    if audition_off then
        quiet(string.char(0x80 | (audition_off.ch - 1), audition_off.note, 64), 0)
        audition_off = nil
    end
end

-- 書いたときに、音源もプロジェクトと同じにする（ふつうに送る：JSFX も覚える）
local function send_tone(ch, msb, lsb, pc)
    for i, m in ipairs(setup.messages(ch, msb, lsb, pc)) do live.send(m, i == 1 and 0 or 1) end
end

-- 鳴らしたチャンネルを元に戻す（試聴だけで送る）：ドラムのパート、音色（今の位置で効いているもの）、
-- ドラムの音ごとの NRPN（キットを変えると音源では既定に戻るので、JSFX が覚えている値を送り直す）、選んでいるもの
local function restore()
    note_off()
    if not preview.active then return end
    local ch = preview.ch
    local st = 0xB0 | (ch - 1)
    local wait = 1
    local pv = rhythm_value(ch)
    if preview.rhythm ~= pv then quiet(sysex.build(0x40, proj.RHYTHM_BLOCK[ch], 0x15, {pv}), 1) wait = 50 end
    local tr = tracks.ch[ch]
    -- 今の位置で効いている音色（位置がセットアップより前なら、セットアップの音色 = 再生するとそれになる）
    local now = tr and (setup.tone_at(proj, tr, r.GetCursorPosition()) or setup.read(proj, tr))
    if now then
        local msgs = setup.messages(ch, now.msb or 0, now.lsb or 0, now.pc)
        quiet(msgs[1], wait); quiet(msgs[2]); quiet(msgs[3])
    end
    local resent = false
    if pv > 0 then
        for msb = 24, 31 do
            for note = 0, 127 do
                local v = live.param_value(ch, "nrpn", msb, note)
                if v and v >= 0 then
                    quiet(string.char(st, 99, msb)); quiet(string.char(st, 98, note)); quiet(string.char(st, 6, v))
                    resent = true
                end
            end
        end
    end
    if resent then
        local mode, msb, lsb = live.selection(ch)
        if mode == "nrpn" and msb and msb >= 0 and lsb and lsb >= 0 then quiet(string.char(st, 99, msb)); quiet(string.char(st, 98, lsb))
        elseif mode == "rpn" and msb and msb >= 0 and lsb and lsb >= 0 then quiet(string.char(st, 101, msb)); quiet(string.char(st, 100, lsb))
        else quiet(string.char(st, 101, 127)); quiet(string.char(st, 100, 127)) end
    end
    preview.active = false
end

-- 選んでいる音色を、選んでいるトラックのチャンネルで鳴らす（そのトラックの設定のまま）
local function audition()
    local t = tone_of(sel)
    if not t or not target then return end
    note_off()
    local ch = target.ch
    if preview.active and preview.ch ~= ch then restore() end
    if not preview.active then preview.ch, preview.active, preview.rhythm = ch, true, rhythm_value(ch) end
    local wait = 1
    if (preview.rhythm > 0) ~= sel.drum then   -- Drum を変えた：そのパートを一時的にドラム（ふつう）にして鳴らす
        local v = sel.drum and 1 or 0
        quiet(sysex.build(0x40, proj.RHYTHM_BLOCK[ch], 0x15, {v}), 1)
        preview.rhythm, wait = v, 50
    end
    local msgs = setup.messages(ch, t.msb, t.lsb, t.pc)
    quiet(msgs[1], wait); quiet(msgs[2]); quiet(msgs[3])
    local note = sel.drum and NOTE.drum or (t.cat == "Bass" and NOTE.bass or NOTE.normal)
    quiet(string.char(0x90 | (ch - 1), note, 100), 1)
    audition_off = {ch = ch, note = note, at = r.time_precise() + AUDITION_S}
end

-- ------------------------------------------------------------------ トラック
-- 選んだものが見えるように、タブと列を送る
local function reveal()
    local tabs = XLABEN_PF_LAYOUT.tabs_visible or 11
    if sel.msb < msb_scroll or sel.msb >= msb_scroll + tabs then msb_scroll = math.max(0, math.min(128 - tabs, sel.msb)) end
    local col = sel.pc // ROWS
    if col < col_scroll or col >= col_scroll + COLS_VISIBLE then col_scroll = math.max(0, math.min(COLS - COLS_VISIBLE, col - COLS_VISIBLE + 1)) end
end

local function load_target(ch, tr)
    if preview.active and preview.ch ~= ch then restore() end
    local tone = setup.read(proj, tr)
    target = {ch = ch, tr = tr, name = proj.track_name(tr), tone = tone}
    sel.drum = rhythm[ch] == true
    if tone then
        sel.msb, sel.lsb, sel.pc = tone.msb or 0, tone.lsb or (sel.drum and 4 or 3), tone.pc
    end
    var_scroll = 0
    reveal()
end

-- 下のボタンに出す、各チャンネルの今の音色
local function scan_channels()
    for ch = 1, 16 do
        local tr = tracks.ch[ch]
        if not tr then
            ch_tones[ch] = nil
        else
            local t = setup.read(proj, tr)
            if t then
                local name = pdb.name_for(db, rhythm[ch] == true, t.lsb or 0, t.msb or 0, t.pc)
                ch_tones[ch] = {name = name or ("PC " .. t.pc), msb = t.msb, lsb = t.lsb, pc = t.pc}
            else
                ch_tones[ch] = false
            end
        end
    end
end

local function rescan(force)
    local change = r.GetProjectStateChangeCount(0)
    local sel_tr = r.GetSelectedTrack(0, 0)
    if not force and change == last_change and sel_tr == last_sel_tr then return end
    local moved = sel_tr ~= last_sel_tr
    local changed = change ~= last_change or force
    last_change, last_sel_tr = change, sel_tr
    tracks = proj.find_tracks()
    rhythm = proj.rhythm_parts(tracks)
    local ch = sel_tr and proj.channel_of(proj.track_name(sel_tr))
    if ch and tracks.ch[ch] == sel_tr then
        if moved or not target or target.ch ~= ch then load_target(ch, sel_tr) end
    elseif target and tracks.ch[target.ch] then
        target.tr = tracks.ch[target.ch]
    else
        -- 選んでいるのが A01〜A16 でない：いちばん若い番号のトラックで鳴らす
        target = nil
        for c = 1, 16 do
            if tracks.ch[c] then load_target(c, tracks.ch[c]) break end
        end
    end
    if target and not moved then target.tone = setup.read(proj, target.tr) end   -- プロジェクトが変わった（元に戻すなど）
    if changed then scan_channels() end
end

-- 選んでいる音色をチャンネル ch のトラックに書いて、音源にも送る
local function write_to(ch)
    local t = tone_of(sel)
    if not t then say("この番号に音色がありません") return false end
    local tr = tracks.ch[ch]
    if not tr then say(string.format("ch%d のトラック（A%02d）がありません", ch, ch)) return false end
    local p = setup.read(proj, tr)
    if not (p and p.msb == t.msb and p.lsb == t.lsb and p.pc == t.pc) then
        local ok, err = setup.write(proj, tr, t, string.format("%s の音色を %s に", proj.short_name(proj.track_name(tr)), t.name))
        if not ok then say(string.format("ch%d：%s", ch, err), 8) return false end
    end
    if target and target.ch == ch then target.tone = setup.read(proj, tr) end
    if preview.active and ch == preview.ch then note_off() preview.active = false end   -- 試聴していたチャンネルに書いた：書いた音色がそのまま正しい
    if preview.active then restore() end            -- ほかのチャンネルで試聴していた：元に戻す
    send_tone(ch, t.msb, t.lsb, t.pc)               -- 音源もプロジェクトと同じに（ふつうに送る）
    ch_flash[ch] = r.time_precise() + 0.8
    last_change = -1                                -- 下のボタンの表示を読み直す
    say(string.format("ch%d に入れました：%s（%d / %d / %d）", ch, t.name, t.msb, t.lsb, t.pc), 4)
    return true
end

-- OK：選んでいるトラックに書く
local function apply()
    if not target then say("A01〜A16 のトラックを選んでください") return false end
    return write_to(target.ch)
end

-- ------------------------------------------------------------------ 選ぶ
local function choose(msb, lsb, pc, play_it)
    sel.msb, sel.lsb, sel.pc = msb or sel.msb, lsb or sel.lsb, pc or sel.pc
    reveal()
    if play_it ~= false and tone_of(sel) then audition() end
end

-- 音色検索：名前に言葉が全部入っているもの。今のマップ → ほかのマップ、ふつうの音色 → ドラムの順に、プログラム・バンクの順
local function search_tones(q)
    local out = {}
    for _, drum in ipairs({sel.drum, not sel.drum}) do
        local list = pdb.search(db, drum, q)
        for _, first in ipairs({true, false}) do
            for _, t in ipairs(list) do
                if (t.lsb == sel.lsb) == first then out[#out + 1] = t end
            end
        end
    end
    return out
end

-- 候補 i を左上の表に出す（play_it なら鳴らす）
local function show_found(i, play_it)
    if #found == 0 then found_i = 0 return end
    found_i = (i - 1) % #found + 1
    local t = found[found_i]
    sel.drum = t.is_drum and true or false
    choose(t.msb, t.lsb, t.pc, play_it)
end

local function on_search_changed()
    found = search_tones(search)
    show_found(1, false)   -- 打っている間は鳴らさない（◀ ▶・Enter で鳴らす）
end

-- ------------------------------------------------------------------ 描く
local function checkbox(label, x, y, on, w, color)
    local box = 12
    fill(C.groove, x, y + 2, box, box)
    frame_rect(C.faint, x, y + 2, box, box)
    if on then fill(C.accent, x + 3, y + 5, box - 6, box - 6) end
    text(label, x + box + 6, y, w - box - 6, 16, "l", color or C.text, 2)
    return inside(x, y, w, 16)
end

local function button(label, x, y, w, h, strong)
    local hov = inside(x, y, w, h)
    fill(strong and C.accent or C.strip, x, y, w, h, hov and 1 or 0.85)
    frame_rect(C.line, x, y, w, h)
    text(label, x, y, w, h, "c", strong and C.bg or C.text, 3)
    return hov
end

local function draw(click, press, wheel)
    local L = {}
    XLABEN_PF_LAYOUT = L
    fill(C.bg, 0, 0, W, H)

    -- ---------------------------------------------------------------- B.MSB のタブ
    text("B.MSB:", 12, 6, 50, 20, "l", C.text, 2)
    local tabs_x, ty, th, tab_w = 64, 6, 20, 44
    local tabs_visible = 11
    L.tabs_visible = tabs_visible
    msb_scroll = math.max(0, math.min(128 - tabs_visible, msb_scroll))
    L.tabs = {}
    for i = 0, tabs_visible - 1 do
        local m = msb_scroll + i
        local x = tabs_x + i * tab_w
        local on = m == sel.msb
        local has = pdb.has_msb(db, sel.drum, sel.lsb, m)
        local hov = inside(x, ty, tab_w - 1, th)
        fill(on and C.accent or (hov and C.strip or C.strip2), x, ty, tab_w - 1, th)
        frame_rect(C.line, x, ty, tab_w - 1, th)
        text(tostring(m), x + 4, ty, tab_w - 8, th, "l", on and C.bg or (has and C.text or C.faint), on and 3 or 2)
        L.tabs[m] = {x, ty, tab_w - 1, th}
        if click and hov then choose(m, nil, nil) end
    end
    local ax = tabs_x + tabs_visible * tab_w + 2
    for k, d in ipairs({{"◀", -1}, {"▶", 1}}) do
        local x = ax + (k - 1) * 15
        local hov = inside(x, ty, 14, th)
        fill(hov and C.strip or C.strip2, x, ty, 14, th)
        frame_rect(C.line, x, ty, 14, th)
        text(d[1], x, ty, 14, th, "c", C.text, 1)
        L[k == 1 and "left" or "right"] = {x, ty, 14, th}
        if click and hov then msb_scroll = math.max(0, math.min(128 - tabs_visible, msb_scroll + d[2])) end
    end

    -- ---------------------------------------------------------------- プログラムの表（21 行 × 4 列。横に送る）
    local gx, gy = 8, 30
    local gw = COLS_VISIBLE * COL_W + 6
    local row_h = 16
    local gh = ROWS * row_h + 6
    fill(C.groove, gx, gy, gw, gh + 16)
    frame_rect(C.line, gx, gy, gw, gh + 16)
    L.rows, L.cols, L.cols_visible = ROWS, COLS, COLS_VISIBLE
    L.grid = {gx, gy, gw, gh}
    if wheel ~= 0 and inside(gx, gy, gw, gh + 16) then
        col_scroll = math.max(0, math.min(COLS - COLS_VISIBLE, col_scroll + (wheel > 0 and -1 or 1)))
    end
    -- 横のスクロールバー（つまみをドラッグ・バーをクリックで 4 列ずつ・ホイール）：表を描く前に動かす
    local bx, by, bw, bh = gx + 20, gy + gh + 2, gw - 40, 12
    local thumb_w = bw * COLS_VISIBLE // COLS
    local span = bw - thumb_w
    local function thumb_at() return bx + span * col_scroll // (COLS - COLS_VISIBLE) end
    if press and inside(thumb_at(), by, thumb_w, bh) then
        drag = {x0 = gfx.mouse_x, scroll0 = col_scroll}
    elseif click and not drag and inside(bx, by, bw, bh) then
        local dir = gfx.mouse_x < thumb_at() and -1 or 1
        col_scroll = math.max(0, math.min(COLS - COLS_VISIBLE, col_scroll + dir * COLS_VISIBLE))
    end
    if drag then
        local dx = gfx.mouse_x - drag.x0
        col_scroll = math.max(0, math.min(COLS - COLS_VISIBLE, math.floor(drag.scroll0 + dx * (COLS - COLS_VISIBLE) / math.max(1, span) + 0.5)))
        if gfx.mouse_cap & 1 == 0 then drag = nil end
    end
    L.cells = {}
    for c = 0, COLS_VISIBLE - 1 do
        local col = col_scroll + c
        local x = gx + 3 + c * COL_W
        for row = 0, ROWS - 1 do
            local pc = col * ROWS + row
            if pc > 127 then break end
            local y = gy + 3 + row * row_h
            local t = pdb.get(db, sel.drum, sel.lsb, sel.msb, pc)
            local on = pc == sel.pc
            local label = string.format("%03d:", pc)
            gfx.setfont(2)
            local lw = gfx.measurestr("000: ")
            local nw = t and math.min(COL_W - lw - 6, gfx.measurestr(t.name) + 4) or 0
            local hov = inside(x, y, COL_W - 4, row_h)
            if on then fill(C.accent, x + lw - 2, y, nw + 2, row_h, 0.9)
            elseif hov and t then fill(C.strip, x + lw - 2, y, nw + 2, row_h) end
            text(label, x, y, lw, row_h, "l", C.dim, 2)
            if t then text(t.name, x + lw, y, COL_W - lw - 6, row_h, "l", on and C.bg or C.text, 2) end
            L.cells[pc] = {x, y, COL_W - 4, row_h}
            if click and hov then choose(nil, nil, pc, t ~= nil) end
        end
    end
    -- 横のスクロールバー（描く）
    fill(C.strip2, bx, by, bw, bh)
    local thumb_x = thumb_at()
    fill(drag and C.accent or C.faint, thumb_x, by + 1, thumb_w, bh - 2)
    L.bar = {bx, by, bw, bh}
    L.thumb = {thumb_x, by, thumb_w, bh}

    -- ---------------------------------------------------------------- 右
    local rx, rw = 583, 197
    text("Variations:", rx + 12, 30, 110, 18, "l", C.text, 2)
    L.drum = {rx + 125, 31, 70, 16}
    if checkbox("Drum", rx + 125, 31, sel.drum, 70) and click then
        sel.drum = not sel.drum
        if not pdb.has_msb(db, sel.drum, sel.lsb, sel.msb) then sel.msb = 0 end
        var_scroll = 0
    end
    local ly, lh, lrow = 50, 6 * 17 + 4, 17
    fill(C.groove, rx, ly, rw, lh)
    frame_rect(C.line, rx, ly, rw, lh)
    local items
    items = pdb.variations(db, sel.drum, sel.pc, (not include_all) and sel.lsb or nil)
    local vis = (lh - 4) // lrow
    var_scroll = math.max(0, math.min(math.max(0, #items - vis), var_scroll))
    if wheel ~= 0 and inside(rx, ly, rw, lh) then
        var_scroll = math.max(0, math.min(math.max(0, #items - vis), var_scroll + (wheel > 0 and -1 or 1)))
    end
    L.vars = {}
    for i = 1, vis do
        local v = items[var_scroll + i]
        if not v then break end
        local vy = ly + 2 + (i - 1) * lrow
        local on = v.msb == sel.msb and v.lsb == sel.lsb and v.pc == sel.pc
        local hov = inside(rx, vy, rw, lrow)
        if on then fill(C.accent, rx + 2, vy, rw - 10, lrow, 0.9)
        elseif hov then fill(C.strip, rx + 2, vy, rw - 10, lrow) end
        local label
        if include_all then label = string.format("%d: [%d] %s", v.msb, v.lsb, v.name)
        else label = string.format("%d: %s", v.msb, v.name) end
        text(label, rx + 4, vy, rw - 14, lrow, "l", on and C.bg or C.text, 2)
        L.vars[i] = {rx, vy, rw - 8, lrow, v}
        if click and hov then choose(v.msb, v.lsb, v.pc) end
    end
    if #items > vis then
        local sh = math.max(12, lh * vis // #items)
        local sy = ly + (lh - sh) * var_scroll // math.max(1, #items - vis)
        fill(C.faint, rx + rw - 6, sy, 4, sh)
    end
    L.include = {rx, 176, rw, 16}
    if checkbox("Include B.LSB Variations", rx, 176, include_all, rw) and click then include_all = not include_all end
    text("B.LSB:", rx, 198, 112, 16, "c", C.text, 2)
    local lsb_y, lsb_w = 216, 112
    fill(C.groove, rx, lsb_y, lsb_w, 4 * 16 + 4)
    frame_rect(C.line, rx, lsb_y, lsb_w, 4 * 16 + 4)
    L.lsb = {}
    for i, m in ipairs(pdb.MAPS) do
        local y = lsb_y + 2 + (i - 1) * 16
        local on = m.lsb == sel.lsb
        local hov = inside(rx, y, lsb_w, 16)
        if on then fill(C.accent, rx + 2, y, lsb_w - 4, 16, 0.9)
        elseif hov then fill(C.strip, rx + 2, y, lsb_w - 4, 16) end
        text(m.name, rx + 4, y, lsb_w - 8, 16, "l", on and C.bg or C.text, 2)
        L.lsb[m.lsb] = {rx, y, lsb_w, 16}
        if click and hov then choose(nil, m.lsb, nil) end
    end
    text("CwBank#:", rx + 124, 226, 72, 16, "c", C.text, 2)
    fill(C.groove, rx + 134, 244, 52, 20)
    frame_rect(C.line, rx + 134, 244, 52, 20)
    text(tostring(pdb.cw_bank(sel.msb, sel.lsb)), rx + 134, 244, 48, 20, "r", C.text, 2)
    -- 音色の検索
    text("音色検索:", rx, 284, rw, 16, "l", C.text, 2)
    local sy = 302
    fill(C.groove, rx, sy, rw, 22)
    set(search_on and C.accent or C.line)
    gfx.rect(rx, sy, rw, 22, 0)
    local shown = search ~= "" and search or (search_on and "" or "（クリックして名前を入力）")
    text(shown .. (search_on and "|" or ""), rx + 4, sy, rw - 30, 22, "l", search ~= "" and C.text or C.faint, 2)
    L.search = {rx, sy, rw - 24, 22}
    if search ~= "" then
        L.clear_search = {rx + rw - 22, sy + 1, 20, 20}
        text("×", rx + rw - 22, sy, 20, 22, "c", C.dim, 3)
        if click and inside(rx + rw - 22, sy, 20, 22) then search, found, found_i = "", {}, 0 end
    end
    if click then search_on = inside(rx, sy, rw - 24, 22) end
    -- 候補を送る・戻す
    local ay = sy + 28
    L.prev = {rx, ay, 34, 22}
    L.next = {rx + rw - 34, ay, 34, 22}
    if button("◀", rx, ay, 34, 22) and click and #found > 0 then show_found(found_i - 1, true) end
    if button("▶", rx + rw - 34, ay, 34, 22) and click and #found > 0 then show_found(found_i + 1, true) end
    local count = search == "" and "" or (#found == 0 and "見つかりません" or string.format("%d / %d", found_i, #found))
    text(count, rx + 36, ay, rw - 72, 22, "c", #found == 0 and C.warn or C.dim, 2)
    L.ok = {rx + 64, 366, 86, 26}
    if button("OK", rx + 64, 366, 86, 26, true) and click then
        if apply() then closing = true end
    end

    -- ---------------------------------------------------------------- 下：選んでいる音色と、トラックに入れるボタン
    local fy = 400
    fill(C.panel, 0, fy, W, H - fy)
    fill(C.line, 0, fy, W, 1)
    local t = tone_of(sel)
    local info = t and string.format("選択：%s　MSB %d / LSB %d / PC %d", t.name, t.msb, t.lsb, t.pc) or "選択：（この番号に音色がありません）"
    local now = r.time_precise()
    local st = now < status_until and status or ""
    text(info, 8, fy + 3, W // 2 - 8, 18, "l", t and C.accent or C.faint, 3)
    if st ~= "" then text(st, W // 2, fy + 3, W // 2 - 8, 18, "r", C.warn, 2) end
    local gap, bw2, bh2 = 4, (W - 16 - 7 * 4) // 8, 44
    L.ch = {}
    for ch = 1, 16 do
        local col, row = (ch - 1) % 8, (ch - 1) // 8
        local x, y = 8 + col * (bw2 + gap), fy + 24 + row * (bh2 + gap)
        local hov = inside(x, y, bw2, bh2)
        local is_drum = rhythm[ch] == true
        local is_target = target and target.ch == ch
        local flash = (ch_flash[ch] or 0) > now
        fill(flash and C.ok or (hov and C.strip or C.strip2), x, y, bw2, bh2)
        set(is_target and C.accent or (is_drum and C.drum or C.line))
        gfx.rect(x, y, bw2, bh2, 0)
        local ct = ch_tones[ch]
        local name = ct and ct.name or (ct == false and "（設定なし）" or "NO TR")
        local function n(v) return v and tostring(v) or "-" end
        local sub = ct and string.format("%s / %s / %s", n(ct.msb), n(ct.lsb), n(ct.pc)) or ""
        text(string.format("Ch%d%s", ch, is_drum and " [D]" or ""), x + 4, y + 1, bw2 - 8, 14, "l",
            flash and C.bg or (is_drum and C.drum or C.text), 3)
        text(name, x + 4, y + 15, bw2 - 8, 14, "l", flash and C.bg or (ct and C.accent or C.faint), 2)
        text(sub, x + 4, y + 29, bw2 - 8, 13, "l", flash and C.bg or C.faint, 1)
        L.ch[ch] = {x, y, bw2, bh2}
        if click and hov then
            if tracks.ch[ch] and not (target and target.ch == ch) then
                local keep = {msb = sel.msb, lsb = sel.lsb, pc = sel.pc, drum = sel.drum}
                load_target(ch, tracks.ch[ch])
                sel.msb, sel.lsb, sel.pc, sel.drum = keep.msb, keep.lsb, keep.pc, keep.drum   -- 選んでいる音色はそのまま
            end
            write_to(ch)
        end
    end
end

-- ------------------------------------------------------------------ キー
local function on_key(c)
    if search_on then
        if c == 8 then search = search:sub(1, -2) on_search_changed()
        elseif c == 27 then search_on = false
        elseif c == 13 then if #found > 0 then show_found(found_i, true) end   -- Enter：今の候補を鳴らす
        elseif c == 30064 or c == 1818584692 then if #found > 0 then show_found(found_i - 1, true) end
        elseif c == 1685026670 or c == 1919379572 then if #found > 0 then show_found(found_i + 1, true) end
        elseif c >= 32 and c < 127 then search = search .. string.char(c) on_search_changed() end
        return
    end
    if c == 27 then closing = true
    elseif c == 13 then if apply() then closing = true end
    elseif c == 32 then play.toggle(MIXER_DIR)
    elseif c == 30064 then choose(nil, nil, math.max(0, sel.pc - 1))             -- ↑
    elseif c == 1685026670 then choose(nil, nil, math.min(127, sel.pc + 1))      -- ↓
    elseif c == 1818584692 then choose(nil, nil, math.max(0, sel.pc - ROWS))     -- ←
    elseif c == 1919379572 then choose(nil, nil, math.min(127, sel.pc + ROWS))   -- →
    end
end

-- ------------------------------------------------------------------ 回す
local finished = false
local function finish()
    if finished then return end   -- 閉じたときと atexit の 2 回呼ばれる
    finished = true
    restore()
    win.finish(EXT)
    local dock, x, y = gfx.dock(-1, 0, 0, 0, 0)
    r.SetExtState(EXT, "pos", string.format("%d,%d,%d", dock, x, y), true)
end

local function loop()
    local c = gfx.getchar()
    if c < 0 or closing then
        finish()
        gfx.quit()
        return
    end
    rescan(false)
    -- 窓の題名：Patch Finder A01（選んでいるトラック）
    local title = TITLE .. (target and (" " .. (proj.track_name(target.tr):match("^%s*(A%d%dR?)") or ("ch" .. target.ch))) or "")
    if title ~= shown_title then gfx.init(title) shown_title = title end
    if gfx.dock(-1) & 1 == 0 and (gfx.w ~= W or gfx.h ~= H) and r.time_precise() - last_resize > 0.2 then
        gfx.init("", W, H)   -- 名前が空なら、大きさだけを変える
        last_resize = r.time_precise()
    end
    while c > 0 do
        on_key(c)
        c = gfx.getchar()
    end
    local cap = gfx.mouse_cap
    local press = cap & 1 == 1 and mouse_was & 1 == 0
    local click = cap & 1 == 0 and mouse_was & 1 == 1
    local right_click = cap & 2 == 0 and mouse_was & 2 == 2
    mouse_was = cap
    if right_click and inside(0, 0, gfx.w, gfx.h) then
        -- 右クリック：ドッキングする・やめる（ミキサー・ドラムと同じ）
        local dock = gfx.dock(-1)
        gfx.x, gfx.y = gfx.mouse_x, gfx.mouse_y
        if gfx.showmenu((dock & 1 == 1 and "!" or "") .. "ドッキング") == 1 then gfx.dock(dock & 1 == 1 and 0 or 1) end
    end
    local wheel = gfx.mouse_wheel
    gfx.mouse_wheel = 0
    if audition_off and r.time_precise() >= audition_off.at then note_off() end
    draw(click, press, wheel)
    local st = tone_of(sel)
    XLABEN_PF_LAYOUT.sel = {msb = sel.msb, lsb = sel.lsb, pc = sel.pc, drum = sel.drum, name = st and st.name or ""}
    XLABEN_PF_LAYOUT.found = {found_i, #found}
    win.track()
    gfx.update()
    r.defer(loop)
end

if not win.start(EXT) then return end
tracks = proj.find_tracks()
if tracks.xlaben then
    local ok, err = proj.ensure_jsfx(tracks.xlaben, MIXER_DIR)
    if not ok then say(err, 10) end
else
    say("「X-Laben」トラックが見つかりません", 10)
end
sync.launch(MIXER_DIR)
local saved = r.GetExtState(EXT, "pos")
local dock, x, y = saved:match("^(%-?%d+),(%-?%d+),(%-?%d+)$")
fonts()
gfx.init(TITLE, W, H, tonumber(dock) or 0, tonumber(x) or 160, tonumber(y) or 160)
rescan(true)
r.atexit(finish)
loop()
