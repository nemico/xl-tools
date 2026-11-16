-- X-Laben EFX Controller for Reaper
-- Lua script for controlling X-Laben EFX parameters via SysEx（部品は lib/）
-- Requires: xlaben_efx_data.json in the same directory

-- ============================================================================
-- Reaper Console Log Toggle
-- true: ログを出す / false: ログを出さない
-- ============================================================================
local ENABLE_REAPER_LOG = reaper.GetExtState("XLabenEFX", "log") == "1"  -- 右クリックのメニューで切り替える
local _original_show_console_msg = reaper.ShowConsoleMsg

reaper.ShowConsoleMsg = function(message)
    if ENABLE_REAPER_LOG then
        _original_show_console_msg(message)
    end
end

-- ============================================================================
-- 部品（lib/）：JSON、パートの番号の対応・試聴・SysEx、値の変換
-- ============================================================================
local SCRIPT_DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])") or ""
local json = dofile(SCRIPT_DIR .. "lib/efx_json.lua")
local efx_midi = dofile(SCRIPT_DIR .. "lib/efx_midi.lua")
local TRACK_TO_SUB, SUB_TO_TRACK, BUTTON_ORDER = efx_midi.TRACK_TO_SUB, efx_midi.SUB_TO_TRACK, efx_midi.BUTTON_ORDER
local midi_utils, live = efx_midi.midi_utils, efx_midi.live
local value_converter = dofile(SCRIPT_DIR .. "lib/efx_values.lua")

-- この画面がアクティブな間も、このスクリプトに割り当てたショートカットで閉じる（REAPER のキーの割り当てを読む）
local ok_keys, keys = pcall(dofile, SCRIPT_DIR .. "../XLabenMixer/lib/xl_keys.lua")
-- Space は XLaben_Play と同じ再生・停止（ミキサーと同じ）
local MIXER_DIR = SCRIPT_DIR .. "../XLabenMixer/"
local ok_play, xl_play = pcall(dofile, MIXER_DIR .. "lib/xl_play.lua")
-- 開く・閉じる・ドッキングの切り替え（ミキサー・ドラムと同じ）
local ok_win, win = pcall(dofile, MIXER_DIR .. "lib/xl_window.lua")
local key_names = ok_keys and keys.own_names(reaper) or {}
local key_bindings, key_read = {}, -100
local other_bindings = {}  -- ミキサー・ドラムに割り当てたショートカット（この画面で押されたら、そちらを実行）
function shortcut_pressed(c)
    if not ok_keys then return false end
    local now = reaper.time_precise()
    if now - key_read > 3 then
        key_read = now
        local f = io.open(reaper.GetResourcePath() .. "/reaper-kb.ini", "rb")
        if f then
            local text = f:read("*a")
            key_bindings, other_bindings = keys.load(text, "efx", key_names)
            f:close()
        end
    end
    return keys.matches(key_bindings, c, gfx.mouse_cap)
end

-- ミキサー・ドラムのショートカットなら、REAPER と同じくそのスクリプトを実行する。実行したら true
function other_shortcut(c)
    if not ok_keys then return false end
    return keys.run_other(reaper, other_bindings, c, gfx.mouse_cap)
end

-- ============================================================================
-- UI Renderer
-- ============================================================================
local ui = {}

ui.WINDOW_WIDTH = 1000
ui.WINDOW_HEIGHT = 660
ui.PADDING = 10
ui.EFX_BUTTONS_HEIGHT = 50
ui.EFX_BUTTON_WIDTH = 50
ui.EFX_BUTTON_HEIGHT = 25
ui.EFX_BUTTON_SPACING = 5
ui.EFFECT_LIST_WIDTH = 300
ui.EFFECT_ROW_HEIGHT = 25
ui.PARAMETER_ROW_HEIGHT = 30
ui.HELP_BOX_HEIGHT = 80
ui.RDC_PANEL_HEIGHT = 60  -- Reverb/Delay/Chorusパネルの高さ
ui.PRESET_PANEL_HEIGHT = 35  -- プリセットボタンパネルの高さ
ui.SLIDER_WIDTH = 300
ui.ADDRESS_WIDTH = 80
ui.NAME_WIDTH = 150
ui.VALUE_WIDTH = 100

ui.COLOR = {
    BACKGROUND = {30, 30, 30},
    TEXT = {220, 220, 220},
    TEXT_DIM = {150, 150, 150},
    TEXT_DISABLED = {100, 100, 100},
    SLIDER_BG = {50, 50, 50},
    SLIDER_FILL = {80, 140, 200},
    SLIDER_HOVER = {100, 160, 220},
    SLIDER_FILL_EFX_A = {80, 200, 80},
    SLIDER_HOVER_EFX_A = {100, 220, 100},
    SLIDER_FILL_EFX_B = {200, 80, 80},
    SLIDER_HOVER_EFX_B = {220, 100, 100},
    BUTTON = {60, 60, 60},
    BUTTON_HOVER = {80, 80, 80},
    DROPDOWN_BG = {45, 45, 50},
    DROPDOWN_ITEM_BG = {55, 55, 60},
    DROPDOWN_HOVER = {70, 75, 85},
    DROPDOWN_DISABLED = {40, 40, 42},
    BORDER = {100, 100, 100},
    PANEL = {40, 40, 45},
    SCROLLBAR_BG = {35, 35, 35},
    SCROLLBAR_THUMB = {90, 90, 90},
    SCROLLBAR_THUMB_HOVER = {120, 120, 120},
    EFX_BUTTON_ON = {80, 200, 80},
    EFX_BUTTON_OFF = {60, 60, 60},
    EFX_BUTTON_ON_HOVER = {100, 220, 100},
    EFX_BUTTON_OFF_HOVER = {80, 80, 80},
    ACCENT = {80, 180, 255},
    -- 下の帯（2026-11-15 に足した。色はほかに合わせた）
    STATUS_BG = {40, 40, 45},
    WARN = {255, 173, 51}
}
ui.STATUS_HEIGHT = 24
ui.MIN_WIDTH, ui.MIN_HEIGHT = 900, 560
ui.DEFAULT_WIDTH, ui.DEFAULT_HEIGHT = 1000, 684

ui.state = {
    window_open = false,
    effect_list_scroll = 0,
    parameter_scroll = 0,
    selected_effect = nil,
    hover_slider = nil,
    hover_parameter = nil,
    hover_effect = nil,
    dragging_slider = nil,
    dragging_effect_scrollbar = false,
    dragging_param_scrollbar = false,
    mouse_down_last = false,
    last_click_time = 0,
    last_clicked_effect = nil,
    -- View mode
    view_mode = "syseff",  -- "efx_a", "efx_b", or "syseff"
    -- System effects scroll
    syseff_reverb_scroll = 0,
    syseff_chorus_scroll = 0,
    syseff_delay_scroll = 0,
    -- Delay tempo sync
    delay_tempo_sync = false,
    delay_sync_note_idx = nil,
    -- Presets
    preset_set_mode = false,     -- set待機モード中か (EFX)
    last_used_preset = nil,      -- 直近で呼び出し/保存したプリセットスロット (EFX)
    syseff_preset_set_mode = { reverb = false, chorus = false, delay = false },
    syseff_last_used_preset = { reverb = nil, chorus = nil, delay = nil }
}

function ui.init(title)
    local w, h, dock, x, y = (reaper.GetExtState("XLabenEFX", "window")):match("(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+),(%-?%d+)")
    gfx.ext_retina = 1
    gfx.init(title or "X-Laben EFX Controller", tonumber(w) or ui.DEFAULT_WIDTH, tonumber(h) or ui.DEFAULT_HEIGHT,
             tonumber(dock) or 0, tonumber(x) or 140, tonumber(y) or 140)
    gfx.setfont(1, "Meiryo UI", 14)
    ui.state.window_open = true
end

function ui.save_window()
    local dock, x, y, w, h = gfx.dock(-1, 0, 0, 0, 0)
    if w and w > 0 then
        reaper.SetExtState("XLabenEFX", "window", string.format("%d,%d,%d,%d,%d", w, h, dock, x, y), true)
    end
end

function ui.close()
    ui.save_window()
    gfx.quit()
    ui.state.window_open = false
end

function ui.set_color(color, alpha)
    if not color then return end
    alpha = alpha or 1.0
    gfx.set(color[1] / 255, color[2] / 255, color[3] / 255, alpha)
end

-- 四角：ミキサー・ドラムと同じく角を丸くする（半径 4）。
-- 画面の幅いっぱいの帯・小さいもの・半透明のもの（丸めると角が重なって濃く見える）は四角のまま
ui.CORNER = 4
function ui.rect(x, y, w, h, filled)
    local rad = math.min(ui.CORNER, w / 2, h / 2)
    local a = type(gfx.a) == "number" and gfx.a or 1
    if w >= gfx.w - 1 or rad < 2 or a < 0.99 then
        gfx.rect(x, y, w, h, filled and 1 or 0)
        return
    end
    if filled then
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

function ui.text(x, y, str, color)
    if not x or not y then
        return
    end
    if color then
        ui.set_color(color)
    end
    gfx.x = x
    gfx.y = y
    gfx.drawstr(tostring(str or ""))
end

function ui.is_mouse_over(x, y, w, h)
    local mx, my = gfx.mouse_x, gfx.mouse_y
    return mx >= x and mx < x + w and my >= y and my < y + h
end

function ui.draw_efx_buttons(x, y, w, h, states, current_effect_name)
    -- 背景パネル
    ui.set_color(ui.COLOR.PANEL)
    ui.rect(x, y, w, h, true)
    
    -- ボーダー
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    -- タイトル（エフェクト名は別で表示するため削除）
    local title = "EFX ON/OFF:"
    ui.text(x + 5, y + 5, title, ui.COLOR.TEXT)
    
    -- ボタンエリア
    local buttons_start_x = x + 10
    local buttons_y = y + 20
    local button_w = ui.EFX_BUTTON_WIDTH
    local button_h = ui.EFX_BUTTON_HEIGHT
    local spacing = ui.EFX_BUTTON_SPACING
    
    local clicked_track = nil
    
    for i, track_num in ipairs(BUTTON_ORDER) do
        local btn_x = buttons_start_x + (i - 1) * (button_w + spacing)
        local btn_y = buttons_y
        
        local state = states[track_num] or 0  -- 0=OFF, 1=A, 2=B
        local hover = ui.is_mouse_over(btn_x, btn_y, button_w, button_h)
        
        -- ボタン背景（3状態）
        if state == 2 then
            -- EFX B ON (赤系)
            ui.set_color(hover and {180, 80, 80} or {150, 60, 60})
        elseif state == 1 then
            -- EFX A ON (緑系)
            ui.set_color(hover and ui.COLOR.EFX_BUTTON_ON_HOVER or ui.COLOR.EFX_BUTTON_ON)
        else
            -- OFF (灰色)
            ui.set_color(hover and ui.COLOR.EFX_BUTTON_OFF_HOVER or ui.COLOR.EFX_BUTTON_OFF)
        end
        ui.rect(btn_x, btn_y, button_w, button_h, true)
        
        -- ボーダー
        ui.set_color(ui.COLOR.BORDER)
        ui.rect(btn_x, btn_y, button_w, button_h, false)
        
        -- ラベル
        local label = tostring(track_num)
        local label_w = gfx.measurestr(label)
        ui.text(btn_x + (button_w - label_w) / 2, 
               btn_y + button_h / 2 - 7, 
               label, ui.COLOR.TEXT)
        
        -- クリック検出
        if hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
            clicked_track = track_num
        end
    end
    
    return clicked_track
end

function ui.draw_view_mode_button(x, y, w, h, current_mode)
    -- 3つのタブボタン: SysEff / EFX A / EFX B
    ui.set_color(ui.COLOR.PANEL)
    ui.rect(x, y, w, h, true)
    
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    local button_w = (w - 20) / 3  -- 3等分
    local button_h = h - 10
    local button_y = y + 5
    
    local clicked_mode = nil
    
    -- SysEffボタン
    local syseff_btn_x = x + 5
    local syseff_hover = ui.is_mouse_over(syseff_btn_x, button_y, button_w, button_h)
    local syseff_active = (current_mode == "syseff")
    
    if syseff_active then
        ui.set_color(ui.COLOR.SLIDER_FILL)
    elseif syseff_hover then
        ui.set_color(ui.COLOR.BUTTON_HOVER)
    else
        ui.set_color(ui.COLOR.BUTTON)
    end
    ui.rect(syseff_btn_x, button_y, button_w, button_h, true)
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(syseff_btn_x, button_y, button_w, button_h, false)
    
    local syseff_label = "SysEff"
    local syseff_label_w = gfx.measurestr(syseff_label)
    ui.text(syseff_btn_x + (button_w - syseff_label_w) / 2, 
           button_y + button_h / 2 - 7, 
           syseff_label, ui.COLOR.TEXT)
    
    if syseff_hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
        clicked_mode = "syseff"
    end
    
    -- EFX A ボタン
    local efx_a_btn_x = syseff_btn_x + button_w + 5
    local efx_a_hover = ui.is_mouse_over(efx_a_btn_x, button_y, button_w, button_h)
    local efx_a_active = (current_mode == "efx_a")
    
    if efx_a_active then
        ui.set_color({45, 92, 45})  -- 緑（アクティブ）
    elseif efx_a_hover then
        ui.set_color(ui.COLOR.BUTTON_HOVER)
    else
        ui.set_color(ui.COLOR.BUTTON)
    end
    ui.rect(efx_a_btn_x, button_y, button_w, button_h, true)
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(efx_a_btn_x, button_y, button_w, button_h, false)
    
    local efx_a_label = "EFX A"
    local efx_a_label_w = gfx.measurestr(efx_a_label)
    ui.text(efx_a_btn_x + (button_w - efx_a_label_w) / 2, 
           button_y + button_h / 2 - 7, 
           efx_a_label, ui.COLOR.TEXT)
    
    if efx_a_hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
        clicked_mode = "efx_a"
    end
    
    -- EFX B ボタン
    local efx_b_btn_x = efx_a_btn_x + button_w + 5
    local efx_b_hover = ui.is_mouse_over(efx_b_btn_x, button_y, button_w, button_h)
    local efx_b_active = (current_mode == "efx_b")
    
    if efx_b_active then
        ui.set_color({92, 45, 45})  -- 赤（アクティブ）
    elseif efx_b_hover then
        ui.set_color(ui.COLOR.BUTTON_HOVER)
    else
        ui.set_color(ui.COLOR.BUTTON)
    end
    ui.rect(efx_b_btn_x, button_y, button_w, button_h, true)
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(efx_b_btn_x, button_y, button_w, button_h, false)
    
    local efx_b_label = "EFX B"
    local efx_b_label_w = gfx.measurestr(efx_b_label)
    ui.text(efx_b_btn_x + (button_w - efx_b_label_w) / 2, 
           button_y + button_h / 2 - 7, 
           efx_b_label, ui.COLOR.TEXT)
    
    if efx_b_hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
        clicked_mode = "efx_b"
    end
    
    return clicked_mode
end

-- ============================================================================
-- Delay Tempo Sync Helper
-- ============================================================================
local DELAY_SYNC_NOTES = {
    { name = "1/64",  beats = 1/16 },
    { name = "1/32T", beats = 1/12 },
    { name = "1/32",  beats = 1/8  },
    { name = "1/16T", beats = 1/6  },
    { name = "1/32D", beats = 3/16 },
    { name = "1/16",  beats = 1/4  },
    { name = "1/8T",  beats = 1/3  },
    { name = "1/16D", beats = 3/8  },
    { name = "1/8",   beats = 1/2  },
    { name = "1/4T",  beats = 2/3  },
    { name = "1/8D",  beats = 3/4  },
    { name = "1/4",   beats = 1.0  },
    { name = "1/2T",  beats = 4/3  },
    { name = "1/4D",  beats = 1.5  },
    { name = "1/2",   beats = 2.0  },
    { name = "1/2D",  beats = 3.0  },
    { name = "1/1",   beats = 4.0  },
}

local function get_initial_bpm()
    -- プロジェクト先頭のタイムマーカーからBPMを取得
    local marker_count = reaper.CountTempoTimeSigMarkers(0)
    if marker_count > 0 then
        local retval, timepos, measurepos, beatpos, bpm = reaper.GetTempoTimeSigMarker(0, 0)
        if retval and bpm and bpm > 0 then
            return bpm
        end
    end
    -- マーカーがない場合はマスターテンポ
    local bpm = reaper.Master_GetTempo()
    if bpm and bpm > 0 then
        return bpm
    end
    return 120.0
end

local function find_closest_delay_time_midi(target_ms, conversion_table)
    if not conversion_table then
        return 97 -- デフォルト 340.0ms
    end
    
    local best_midi = 1
    local best_diff = 999999
    
    for k, v in pairs(conversion_table) do
        local midi_num = tonumber(k)
        local ms_num = tonumber(string.match(tostring(v), "([%d%.]+)"))
        if midi_num and ms_num then
            local diff = math.abs(target_ms - ms_num)
            if diff < best_diff then
                best_diff = diff
                best_midi = midi_num
            end
        end
    end
    
    return best_midi
end

local function get_closest_note_index(current_midi, bpm, conversion_table)
    local current_ms = 340.0
    if conversion_table and conversion_table[tostring(current_midi)] then
        current_ms = tonumber(string.match(tostring(conversion_table[tostring(current_midi)]), "([%d%.]+)")) or 340.0
    end
    
    local beat_ms = (60.0 / bpm) * 1000.0
    local best_idx = 12 -- デフォルト 1/4
    local best_diff = 999999
    
    for idx, note in ipairs(DELAY_SYNC_NOTES) do
        local target_ms = note.beats * beat_ms
        local diff = math.abs(current_ms - target_ms)
        if diff < best_diff then
            best_diff = diff
            best_idx = idx
        end
    end
    
    return best_idx
end

-- ============================================================================
-- System Effect Macro Presets (PhoenixMAX / Sound Canvas VA 準拠)
-- ============================================================================

local DEFAULT_SYSEFF_MACRO_PRESETS = {
    reverb = {
        ["0"] = { ["31"] = 0, ["32"] = 3, ["33"] = 64, ["34"] = 80, ["37"] = 0 }, -- Room 1
        ["1"] = { ["31"] = 1, ["32"] = 4, ["33"] = 64, ["34"] = 56, ["37"] = 0 }, -- Room 2
        ["2"] = { ["31"] = 2, ["32"] = 0, ["33"] = 64, ["34"] = 64, ["37"] = 0 }, -- Room 3
        ["3"] = { ["31"] = 3, ["32"] = 4, ["33"] = 64, ["34"] = 72, ["37"] = 0 }, -- Hall 1
        ["4"] = { ["31"] = 4, ["32"] = 0, ["33"] = 64, ["34"] = 64, ["37"] = 0 }, -- Hall 2
        ["5"] = { ["31"] = 5, ["32"] = 0, ["33"] = 64, ["34"] = 88, ["37"] = 0 }, -- Plate
        ["6"] = { ["31"] = 0, ["32"] = 0, ["33"] = 0,  ["34"] = 0,  ["37"] = 0 }, -- Delay
        ["7"] = { ["31"] = 0, ["32"] = 3, ["33"] = 0,  ["34"] = 0,  ["37"] = 0 }  -- Panning Delay
    },
    chorus = {
        ["0"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 0,   ["3C"] = 112, ["3D"] = 3, ["3E"] = 5,   ["3F"] = 0, ["40"] = 0 }, -- Chorus 1
        ["1"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 0,   ["3C"] = 80,  ["3D"] = 9, ["3E"] = 19,  ["3F"] = 0, ["40"] = 0 }, -- Chorus 2
        ["2"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 8,   ["3C"] = 80,  ["3D"] = 3, ["3E"] = 19,  ["3F"] = 0, ["40"] = 0 }, -- Chorus 3
        ["3"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 16,  ["3C"] = 64,  ["3D"] = 9, ["3E"] = 16,  ["3F"] = 0, ["40"] = 0 }, -- Chorus 4
        ["4"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 64,  ["3C"] = 127, ["3D"] = 2, ["3E"] = 24,  ["3F"] = 0, ["40"] = 0 }, -- Feedback Chorus
        ["5"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 112, ["3C"] = 127, ["3D"] = 1, ["3E"] = 5,   ["3F"] = 0, ["40"] = 0 }, -- Flanger
        ["6"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 0,   ["3C"] = 127, ["3D"] = 0, ["3E"] = 127, ["3F"] = 0, ["40"] = 0 }, -- Short Delay
        ["7"] = { ["39"] = 0, ["3A"] = 64, ["3B"] = 80,  ["3C"] = 127, ["3D"] = 0, ["3E"] = 127, ["3F"] = 0, ["40"] = 0 }  -- Short Delay (FB)
    },
    delay = {
        ["0"] = { ["51"] = 0, ["52"] = 97,  ["53"] = 1,  ["54"] = 1,  ["55"] = 127, ["56"] = 0,   ["57"] = 0,  ["58"] = 64, ["59"] = 80, ["5A"] = 0 },  -- Delay 1
        ["1"] = { ["51"] = 0, ["52"] = 106, ["53"] = 1,  ["54"] = 1,  ["55"] = 127, ["56"] = 0,   ["57"] = 0,  ["58"] = 64, ["59"] = 80, ["5A"] = 0 },  -- Delay 2
        ["2"] = { ["51"] = 0, ["52"] = 115, ["53"] = 1,  ["54"] = 1,  ["55"] = 127, ["56"] = 0,   ["57"] = 0,  ["58"] = 64, ["59"] = 72, ["5A"] = 0 },  -- Delay 3
        ["3"] = { ["51"] = 0, ["52"] = 83,  ["53"] = 1,  ["54"] = 1,  ["55"] = 127, ["56"] = 0,   ["57"] = 0,  ["58"] = 64, ["59"] = 72, ["5A"] = 0 },  -- Delay 4
        ["4"] = { ["51"] = 0, ["52"] = 105, ["53"] = 12, ["54"] = 1,  ["55"] = 0,   ["56"] = 125, ["57"] = 60, ["58"] = 64, ["59"] = 74, ["5A"] = 0 },  -- Pan Delay 1
        ["5"] = { ["51"] = 0, ["52"] = 109, ["53"] = 12, ["54"] = 24, ["55"] = 0,   ["56"] = 125, ["57"] = 60, ["58"] = 64, ["59"] = 71, ["5A"] = 0 },  -- Pan Delay 2
        ["6"] = { ["51"] = 0, ["52"] = 115, ["53"] = 12, ["54"] = 24, ["55"] = 0,   ["56"] = 120, ["57"] = 64, ["58"] = 64, ["59"] = 73, ["5A"] = 0 },  -- Pan Delay 3
        ["7"] = { ["51"] = 0, ["52"] = 93,  ["53"] = 12, ["54"] = 24, ["55"] = 0,   ["56"] = 120, ["57"] = 64, ["58"] = 64, ["59"] = 72, ["5A"] = 0 },  -- Pan Delay 4
        ["8"] = { ["51"] = 0, ["52"] = 109, ["53"] = 12, ["54"] = 24, ["55"] = 0,   ["56"] = 114, ["57"] = 60, ["58"] = 64, ["59"] = 61, ["5A"] = 36 }, -- Delay to Reverb
        ["9"] = { ["51"] = 0, ["52"] = 110, ["53"] = 21, ["54"] = 32, ["55"] = 97,  ["56"] = 127, ["57"] = 67, ["58"] = 64, ["59"] = 40, ["5A"] = 0 }   -- Pan Repeat
    }
}

local function apply_syseff_macro_presets(section_name, section_data, macro_value, updated_values)
    local presets = (section_data and section_data.macro_presets) or DEFAULT_SYSEFF_MACRO_PRESETS[section_name]
    if not presets then return end
    
    local preset = presets[tostring(macro_value)] or presets[macro_value]
    if not preset then return end
    
    for _, p in ipairs(section_data.parameters) do
        local addr = p.address
        local sub = tonumber(addr.sub, 16) or 0
        local offset = tonumber(addr.offset, 16) or 0
        local offset_key = string.format("%02X", offset)
        
        local target_val = preset[offset_key]
        if target_val == nil then
            target_val = preset[offset] or preset[tostring(offset)]
        end
        
        if target_val ~= nil then
            local p_id = string.format("%s_%02X_%02X", section_name, sub, offset)
            app.system_effect_values[p_id] = target_val
            updated_values[p_id] = { param = p, value = target_val }
        end
    end
end

function ui.draw_syseff_section_panel(x, y, w, h, section_name, section_data, scroll_state)
    -- System Effect section panel (Reverb/Chorus/Delay)
    ui.set_color(ui.COLOR.PANEL)
    ui.rect(x, y, w, h, true)
    
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    -- タイトル
    ui.set_color(ui.COLOR.TEXT)
    ui.text(x + 5, y + 5, section_data.name, ui.COLOR.TEXT)
    
    local updated_values = {}
    
    -- ディレイタイトルの行に右寄せでBPM Syncチェックボックスを配置
    if section_name == "delay" then
        local bpm = get_initial_bpm()
        local cb_label = string.format("BPM Sync (%.1f)", bpm)
        local cb_w = 115
        local cb_h = 18
        local cb_x = x + w - cb_w - 6
        local cb_y = y + 5
        
        local is_hover = ui.is_mouse_over(cb_x, cb_y, cb_w, cb_h)
        local mouse_down = (gfx.mouse_cap & 1 == 1)
        
        if is_hover and mouse_down and not ui.state.mouse_down_last then
            ui.state.delay_tempo_sync = not ui.state.delay_tempo_sync
            if ui.state.delay_tempo_sync then
                -- ONになった瞬間に、最寄りの音符のMIDI値にスナップして更新
                local tc_param = nil
                for _, p in ipairs(section_data.parameters) do
                    local p_off = tonumber(p.address.offset, 16) or 0
                    if p_off == 0x52 then
                        tc_param = p
                        break
                    end
                end
                if tc_param then
                    local tc_param_id = "delay_01_52"
                    local current_tc_val = app and app.system_effect_values and app.system_effect_values[tc_param_id] or tonumber(tc_param.default_hex, 16) or 97
                    local note_idx = get_closest_note_index(current_tc_val, bpm, tc_param.conversion_table)
                    ui.state.delay_sync_note_idx = note_idx
                    local chosen_note = DELAY_SYNC_NOTES[note_idx]
                    local target_ms = chosen_note.beats * (60000.0 / bpm)
                    local snapped_val = find_closest_delay_time_midi(target_ms, tc_param.conversion_table)
                    if app and app.system_effect_values then
                        app.system_effect_values[tc_param_id] = snapped_val
                    end
                    updated_values[tc_param_id] = {param = tc_param, value = snapped_val}
                end
            else
                ui.state.delay_sync_note_idx = nil
            end
        end
        
        -- チェックボックス枠描画
        local box_size = 12
        local box_x = cb_x
        local box_y = cb_y + 3
        
        ui.set_color(ui.COLOR.BUTTON)
        ui.rect(box_x, box_y, box_size, box_size, true)
        ui.set_color(is_hover and ui.COLOR.BUTTON_HOVER or ui.COLOR.BORDER)
        ui.rect(box_x, box_y, box_size, box_size, false)
        
        if ui.state.delay_tempo_sync then
            ui.set_color(ui.COLOR.ACCENT)
            gfx.x = box_x + 2
            gfx.y = box_y - 2
            gfx.drawstr("x")
        end
        
        ui.text(box_x + box_size + 4, cb_y + 1, cb_label, is_hover and ui.COLOR.ACCENT or ui.COLOR.TEXT)
    end
    
    local preset_area_h = 66
    local list_y = y + 30
    local list_h = h - 30 - preset_area_h
    local row_h = ui.PARAMETER_ROW_HEIGHT
    local visible_count = math.floor(list_h / row_h)
    local scroll = scroll_state or 0
    local max_scroll = math.max(0, #section_data.parameters - visible_count)
    
    -- Draw parameters
    for i = scroll + 1, math.min(scroll + visible_count, #section_data.parameters) do
        local param = section_data.parameters[i]
        local row_y = list_y + (i - scroll - 1) * row_h
        
        -- Generate param_id
        local addr = param.address
        local sub = tonumber(addr.sub, 16) or 0
        local offset = tonumber(addr.offset, 16) or 0
        local param_id = string.format("%s_%02X_%02X", section_name, sub, offset)
        
        local def_v = param.default or (param.type_info and param.type_info.default) or tonumber(param.default_hex, 16) or 0
        local value = app.system_effect_values[param_id] or def_v
        
        -- Draw parameter row
        local bg_alpha = 0.1
        ui.set_color(ui.COLOR.BUTTON, bg_alpha)
        ui.rect(x, row_y, w, row_h, true)
        
        local hover = ui.is_mouse_over(x, row_y, w, row_h)
        if hover then
            ui.state.hover_parameter = param
        end
        
        -- Parameter name (remove section prefix and simplify)
        local display_name = param.name
        if section_name == "reverb" then
            display_name = display_name:gsub("^REVERB ", "")
        elseif section_name == "chorus" then
            display_name = display_name:gsub("^CHORUS ", "")
        elseif section_name == "delay" then
            display_name = display_name:gsub("^DELAY ", "")
        end
        -- Simplify "SEND LEVEL" patterns
        display_name = display_name:gsub("^SEND LEVEL ", "")
        
        local name_x = x + 5
        ui.text(name_x, row_y + 8, display_name, ui.COLOR.TEXT)
        
        -- Slider with proper range based on parameter type
        local slider_x = x + w - 220
        local slider_y = row_y + 5
        local slider_w = 150
        local slider_h = 20
        local value_x = slider_x + slider_w + 10
        
        -- パラメーター型に応じた範囲を決定
        local min_val = 0
        local max_val = 127
        local type_info = param.type_info
        
        -- SysEff特有: Pre-LowpassとCharacterは0-7の8段階（MIDI値も0-7）
        local is_small_range = false
        if type_info and type_info.type == "numeric_range" then
            local param_max = type_info.max or 127
            -- maxが小さい場合（0-7、0-15など）は特別処理
            if param_max <= 15 then
                min_val = 0
                max_val = math.floor(param_max)
                is_small_range = true
            end
        end
        
        -- 上記以外の通常パラメーターの範囲設定
        if not is_small_range then
            -- conversion_tableまたはテーブル型がある場合
            if param.conversion_table or (type_info and type_info.type == "table") then
                if type_info and type_info.min and type_info.max then
                    min_val = type_info.min
                    max_val = type_info.max
                else
                    min_val = 0
                    max_val = 127
                end
            elseif type_info and type_info.type == "discrete" then
                -- 離散値：選択肢の数-1が最大値
                if type_info.choices then
                    max_val = #type_info.choices - 1
                end
            elseif type_info and type_info.type == "numeric_range" then
                -- 整数範囲の場合、段階数に応じてフェーダー範囲を設定
                local param_min = type_info.min or 0
                local param_max = type_info.max or 127
                -- 整数かどうかチェック
                if param_min == math.floor(param_min) and param_max == math.floor(param_max) then
                    local range = param_max - param_min
                    -- 範囲が小さい場合（例：0～127 = 128段階より小さい）
                    if range <= 127 and not (param.name and string.find(param.name, "Rate")) then
                        min_val = 0
                        max_val = range
                    end
                end
            end
        end
        
        local is_time_center = (section_name == "delay" and offset == 0x52)
        local is_sync_active = (is_time_center and ui.state.delay_tempo_sync)
        
        local slider_id = param_id
        local new_value = value
        local display_value = ""
        
        if is_sync_active then
            local bpm = get_initial_bpm()
            local note_idx = ui.state.delay_sync_note_idx
            if not note_idx or note_idx < 1 or note_idx > #DELAY_SYNC_NOTES then
                note_idx = get_closest_note_index(value, bpm, param.conversion_table)
                ui.state.delay_sync_note_idx = note_idx
            end
            
            local fake_param = { type_info = { type = "discrete" }, default = 12 }
            
            local new_note_idx = ui.draw_slider(slider_x, slider_y, slider_w, slider_h, 
                note_idx, 1, #DELAY_SYNC_NOTES, slider_id, fake_param)
            
            new_note_idx = math.max(1, math.min(#DELAY_SYNC_NOTES, new_note_idx))
            
            if new_note_idx ~= note_idx then
                ui.state.delay_sync_note_idx = new_note_idx
                note_idx = new_note_idx
                local chosen_note = DELAY_SYNC_NOTES[new_note_idx]
                local target_ms = chosen_note.beats * (60000.0 / bpm)
                new_value = find_closest_delay_time_midi(target_ms, param.conversion_table)
                updated_values[param_id] = {param = param, value = new_value}
                app.system_effect_values[param_id] = new_value
                value = new_value
            end
            
            local current_note = DELAY_SYNC_NOTES[note_idx]
            display_value = current_note.name
        else
            new_value = ui.draw_slider(slider_x, slider_y, slider_w, slider_h, 
                value, min_val, max_val, slider_id, param)
            
            if new_value ~= value then
                updated_values[param_id] = {param = param, value = new_value}
                app.system_effect_values[param_id] = new_value
                value = new_value
                
                -- Macro変更時に他の全パラメーターをマクロプリセット値へ自動連動
                local is_macro = (param.name and string.find(param.name, "MACRO")) or
                                 (section_name == "reverb" and offset == 0x30) or
                                 (section_name == "chorus" and offset == 0x38) or
                                 (section_name == "delay" and offset == 0x50)
                if is_macro then
                    apply_syseff_macro_presets(section_name, section_data, new_value, updated_values)
                    if section_name == "delay" then
                        ui.state.delay_sync_note_idx = nil
                    end
                end
            end
            
            display_value = convert_syseff_value_to_display(param, value)
        end
        
        -- MIDIイベントから読み取った値が存在するかチェック
        -- app.system_effect_valuesがread_system_effect_parametersで設定されている場合のみ、そのキーが存在する
        -- 初期化時にデフォルト値で設定された場合とMIDIから読み取られた場合を区別するため、
        -- MIDIから読み取られたパラメータのリストをチェック
        -- （前は 1 コマごと・1 行ごとに SysEx を全部読み直していた。読み直すのは read_system_effect_parameters のときだけにした）
        local found_in_midi = app.midi_take ~= nil and (app.syseff_in_midi or {})[sub * 256 + offset] == true
        
        -- MIDIイベントに存在しない場合は表示値に「!」を付ける
        if not found_in_midi then
            display_value = display_value .. " !"
        end
        
        ui.text(value_x, row_y + 8, display_value, ui.COLOR.TEXT)
    end
    
    -- Scroll handling
    if ui.is_mouse_over(x, list_y, w, list_h) then
        local scroll_delta = gfx.mouse_wheel
        if scroll_delta ~= 0 then
            scroll = math.max(0, math.min(max_scroll, scroll - scroll_delta))
            gfx.mouse_wheel = 0
        end
    end

    -- プリセットボタンエリア (セクションパネル下部に固定)
    local preset_y = y + h - preset_area_h
    
    -- 区切り境界線
    ui.set_color(ui.COLOR.BORDER)
    gfx.line(x, preset_y, x + w, preset_y)

    local btn_w = 45
    local btn_h = 24
    local spacing_x = 5
    local spacing_y = 4
    local start_x = x + 12
    local row1_y = preset_y + 6
    local row2_y = row1_y + btn_h + spacing_y

    local is_set_mode = (ui.state.syseff_preset_set_mode and ui.state.syseff_preset_set_mode[section_name]) or false
    local key = "syseff_" .. section_name
    local eff_presets = (app.presets and app.presets[key]) or {}
    local last_used = (ui.state.syseff_last_used_preset and ui.state.syseff_last_used_preset[section_name])

    gfx.setfont(1, "Meiryo UI", 14)

    -- 1. "Set" ボタン (行1 左端)
    local set_btn_x = start_x
    local set_btn_y = row1_y
    local set_hover = ui.is_mouse_over(set_btn_x, set_btn_y, btn_w, btn_h)

    if is_set_mode then
        -- SETモード待機中: 黄色強調表示
        ui.set_color(set_hover and {255, 230, 80} or {220, 190, 30})
        ui.rect(set_btn_x, set_btn_y, btn_w, btn_h, true)
        ui.set_color({255, 255, 140})
        ui.rect(set_btn_x, set_btn_y, btn_w, btn_h, false)
        local lbl_w = gfx.measurestr("Set")
        ui.text(set_btn_x + (btn_w - lbl_w) / 2, set_btn_y + btn_h / 2 - 7, "Set", {20, 20, 20})
    else
        -- 通常時
        ui.set_color(set_hover and ui.COLOR.BUTTON_HOVER or ui.COLOR.BUTTON)
        ui.rect(set_btn_x, set_btn_y, btn_w, btn_h, true)
        ui.set_color(ui.COLOR.BORDER)
        ui.rect(set_btn_x, set_btn_y, btn_w, btn_h, false)
        local lbl_w = gfx.measurestr("Set")
        ui.text(set_btn_x + (btn_w - lbl_w) / 2, set_btn_y + btn_h / 2 - 7, "Set", ui.COLOR.TEXT)
    end

    if set_hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
        if not ui.state.syseff_preset_set_mode then ui.state.syseff_preset_set_mode = {} end
        ui.state.syseff_preset_set_mode[section_name] = not is_set_mode
    end

    -- 行2 左端 (Setボタンの下): "Preset" ラベル
    local p_lbl = "Preset"
    local p_lbl_w = gfx.measurestr(p_lbl)
    ui.text(start_x + (btn_w - p_lbl_w) / 2, row2_y + btn_h / 2 - 7, p_lbl, ui.COLOR.TEXT_DIM)

    -- 2. "1" 〜 "10" ボタン
    for slot = 1, 10 do
        local slot_key = tostring(slot)
        local has_preset = (eff_presets[slot_key] ~= nil)
        local is_active = (last_used == slot and not is_set_mode)

        local btn_x, btn_y
        if slot <= 5 then
            btn_x = start_x + slot * (btn_w + spacing_x)
            btn_y = row1_y
        else
            btn_x = start_x + (slot - 5) * (btn_w + spacing_x)
            btn_y = row2_y
        end

        local hover = ui.is_mouse_over(btn_x, btn_y, btn_w, btn_h)

        if is_set_mode then
            -- SET待機中: すべての1-10ボタンを黄色にして待機！
            ui.set_color(hover and {255, 230, 80} or {220, 190, 30})
            ui.rect(btn_x, btn_y, btn_w, btn_h, true)
            ui.set_color({255, 255, 120})
            ui.rect(btn_x, btn_y, btn_w, btn_h, false)

            local label = tostring(slot)
            local l_w = gfx.measurestr(label)
            ui.text(btn_x + (btn_w - l_w) / 2, btn_y + btn_h / 2 - 7, label, {20, 20, 20})

            if hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
                save_syseff_preset_slot(section_name, slot)
                if ui.state.syseff_preset_set_mode then
                    ui.state.syseff_preset_set_mode[section_name] = false
                end
            end
        else
            -- 通常時: 呼び出し
            if has_preset then
                -- 登録済みスロット: スレートブルー系
                ui.set_color(hover and {75, 100, 135} or {55, 75, 105})
                ui.rect(btn_x, btn_y, btn_w, btn_h, true)
                if is_active then
                    ui.set_color({80, 220, 240}) -- アクティブ枠線
                else
                    ui.set_color(hover and {100, 135, 180} or {80, 110, 140})
                end
                ui.rect(btn_x, btn_y, btn_w, btn_h, false)

                local label = tostring(slot)
                local l_w = gfx.measurestr(label)
                ui.text(btn_x + (btn_w - l_w) / 2, btn_y + btn_h / 2 - 7, label, {240, 240, 240})
            else
                -- 未登録スロット: 暗い灰色
                ui.set_color(hover and {55, 55, 55} or {40, 40, 40})
                ui.rect(btn_x, btn_y, btn_w, btn_h, true)
                ui.set_color({60, 60, 60})
                ui.rect(btn_x, btn_y, btn_w, btn_h, false)

                local label = tostring(slot)
                local l_w = gfx.measurestr(label)
                ui.text(btn_x + (btn_w - l_w) / 2, btn_y + btn_h / 2 - 7, label, {110, 110, 110})
            end

            if hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
                if has_preset then
                    load_syseff_preset_slot(section_name, slot)
                end
            end
        end
    end
    
    return updated_values, scroll
end

function ui.draw_syseff_view()
    -- Draw system effects view (B面)
    local panel_w = (ui.WINDOW_WIDTH - ui.PADDING * 4) / 3
    local panel_h = ui.WINDOW_HEIGHT - ui.PADDING * 3 - ui.EFX_BUTTONS_HEIGHT - 40 - ui.HELP_BOX_HEIGHT
    local panel_y = ui.PADDING + ui.EFX_BUTTONS_HEIGHT + ui.PADDING + 40
    
    if not app.system_effects then
        ui.text(ui.PADDING, panel_y, "System Effects database not loaded", ui.COLOR.TEXT)
        return {}
    end
    
    local all_updates = {}
    
    -- Reverb panel
    local reverb_x = ui.PADDING
    local reverb_data = app.system_effects.sections.reverb
    if reverb_data then
        local updates, new_scroll = ui.draw_syseff_section_panel(
            reverb_x, panel_y, panel_w, panel_h,
            "reverb", reverb_data, ui.state.syseff_reverb_scroll
        )
        ui.state.syseff_reverb_scroll = new_scroll
        for k, v in pairs(updates) do
            all_updates[k] = v
        end
    end
    
    -- Chorus panel
    local chorus_x = reverb_x + panel_w + ui.PADDING
    local chorus_data = app.system_effects.sections.chorus
    if chorus_data then
        local updates, new_scroll = ui.draw_syseff_section_panel(
            chorus_x, panel_y, panel_w, panel_h,
            "chorus", chorus_data, ui.state.syseff_chorus_scroll
        )
        ui.state.syseff_chorus_scroll = new_scroll
        for k, v in pairs(updates) do
            all_updates[k] = v
        end
    end
    
    -- Delay panel
    local delay_x = chorus_x + panel_w + ui.PADDING
    local delay_data = app.system_effects.sections.delay
    if delay_data then
        local updates, new_scroll = ui.draw_syseff_section_panel(
            delay_x, panel_y, panel_w, panel_h,
            "delay", delay_data, ui.state.syseff_delay_scroll
        )
        ui.state.syseff_delay_scroll = new_scroll
        for k, v in pairs(updates) do
            all_updates[k] = v
        end
    end
    
    -- Help box
    local help_box_x = ui.PADDING
    local help_box_y = panel_y + panel_h + ui.PADDING
    local help_box_w = ui.WINDOW_WIDTH - ui.PADDING * 2
    local help_box_h = ui.HELP_BOX_HEIGHT
    
    ui.draw_help_box(
        help_box_x, help_box_y,
        help_box_w, help_box_h,
        nil, ui.state.hover_parameter
    )
    
    return all_updates
end

function ui.draw_effect_list(x, y, w, h, effects, selected_index, is_playing, view_mode)
    -- 背景
    ui.set_color(ui.COLOR.DROPDOWN_BG)
    ui.rect(x, y, w, h, true)
    
    -- ボーダー
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    -- タイトル
    ui.set_color(ui.COLOR.TEXT)
    ui.text(x + 5, y + 5, "Effects:", ui.COLOR.TEXT)
    
    local list_y = y + 30
    local list_h = h - 30
    local visible_items = math.floor(list_h / ui.EFFECT_ROW_HEIGHT)
    local scroll = ui.state.effect_list_scroll
    local max_scroll = math.max(0, #effects - visible_items)
    
    -- スクロールバーエリアの判定用
    local scrollbar_w = 8
    local scrollbar_x = x + w - scrollbar_w - 2
    local is_in_scrollbar = ui.is_mouse_over(scrollbar_x, list_y, scrollbar_w + 2, list_h)
    
    -- エフェクトアイテム描画
    local clicked_index = nil
    local double_clicked_index = nil
    local current_time = reaper.time_precise()
    
    for i = scroll + 1, math.min(scroll + visible_items, #effects) do
        local item_y = list_y + (i - scroll - 1) * ui.EFFECT_ROW_HEIGHT
        local effect = effects[i]
        local effect_no = tonumber(effect.no) or 0
        local item_text = string.format("%02d: %s", effect_no, effect.name)
        
        -- グレーアウト判定
        -- NGEFXが1の場合、またはEFX Bのときは65のOD3以外を無効化
        local is_disabled = (effect.ngefx == 1) or (view_mode == "efx_b" and effect_no ~= 65)
        local hover = ui.is_mouse_over(x, item_y, w, ui.EFFECT_ROW_HEIGHT)
        
        -- ホバー検出（無効なエフェクトでも説明を表示）
        if hover and not is_playing then
            ui.state.hover_effect = effect
        end
        
        -- 背景色
        if is_disabled then
            ui.set_color(ui.COLOR.DROPDOWN_DISABLED)
            ui.rect(x, item_y, w, ui.EFFECT_ROW_HEIGHT, true)
        elseif i == selected_index then
            ui.set_color(ui.COLOR.SLIDER_FILL)
            ui.rect(x, item_y, w, ui.EFFECT_ROW_HEIGHT, true)
        elseif hover and not is_playing then
            ui.set_color(ui.COLOR.DROPDOWN_HOVER)
            ui.rect(x, item_y, w, ui.EFFECT_ROW_HEIGHT, true)
        else
            ui.set_color(ui.COLOR.DROPDOWN_ITEM_BG)
            ui.rect(x, item_y, w, ui.EFFECT_ROW_HEIGHT, true)
        end
        
        -- クリック処理（再生中、スクロールバーエリア、無効エフェクトを除外）
        if not is_playing and hover and not is_disabled and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last and not is_in_scrollbar then
            clicked_index = i
            
            -- ダブルクリック判定（300ms以内に同じアイテムをクリック）
            if ui.state.last_clicked_effect == i and (current_time - ui.state.last_click_time) < 0.3 then
                double_clicked_index = i
                ui.state.last_clicked_effect = nil
                ui.state.last_click_time = 0
            else
                ui.state.last_clicked_effect = i
                ui.state.last_click_time = current_time
            end
        end
        
        -- テキスト
        local text_color = is_disabled and ui.COLOR.TEXT_DISABLED or ui.COLOR.TEXT
        ui.text(x + 5, item_y + 5, item_text, text_color)
    end
    
    -- 再生中のグレーオーバーレイ
    if is_playing then
        gfx.set(0.2, 0.2, 0.2, 0.7)
        gfx.rect(x, list_y, w, list_h, true)
        
        -- 「再生中は変更不可」メッセージ
        gfx.set(0.8, 0.8, 0.8, 1)
        gfx.setfont(1, "Meiryo UI", 14)
        local msg = "Cannot change effect during playback"
        local text_w, text_h = gfx.measurestr(msg)
        gfx.x = x + (w - text_w) / 2
        gfx.y = list_y + (list_h - text_h) / 2
        gfx.drawstr(msg)
    end
    
    -- マウスホイール処理（再生中もスクロールは有効）
    if ui.is_mouse_over(x, list_y, w, list_h) then
        local scroll_delta = gfx.mouse_wheel
        if scroll_delta ~= 0 then
            local scroll_amount = (scroll_delta > 0) and -1 or 1
            ui.state.effect_list_scroll = math.max(0, math.min(max_scroll, 
                ui.state.effect_list_scroll + scroll_amount))
            gfx.mouse_wheel = 0
        end
    end
    
    -- スクロールバー
    if max_scroll > 0 then
        local scrollbar_w = 8
        local scrollbar_x = x + w - scrollbar_w - 2
        local scrollbar_y = list_y + 2
        local scrollbar_h = list_h - 4
        
        -- スクロールバー背景
        ui.set_color(ui.COLOR.SCROLLBAR_BG)
        ui.rect(scrollbar_x, scrollbar_y, scrollbar_w, scrollbar_h, true)
        
        -- スクロールバーつまみ
        local thumb_height = math.max(20, scrollbar_h * visible_items / #effects)
        local thumb_y = scrollbar_y + (scrollbar_h - thumb_height) * (scroll / max_scroll)
        
        local thumb_hover = ui.is_mouse_over(scrollbar_x, thumb_y, scrollbar_w, thumb_height)
        local mouse_down = gfx.mouse_cap & 1 == 1
        
        -- ドラッグ開始
        if thumb_hover and mouse_down and not ui.state.mouse_down_last then
            ui.state.dragging_effect_scrollbar = true
        end
        
        -- ドラッグ中
        if ui.state.dragging_effect_scrollbar and mouse_down then
            local mouse_y = gfx.mouse_y
            local relative_y = mouse_y - scrollbar_y - thumb_height / 2
            local scroll_ratio = relative_y / (scrollbar_h - thumb_height)
            scroll_ratio = math.max(0, math.min(1, scroll_ratio))
            ui.state.effect_list_scroll = math.floor(scroll_ratio * max_scroll + 0.5)
        end
        
        -- ドラッグ終了
        if not mouse_down then
            ui.state.dragging_effect_scrollbar = false
        end
        
        ui.set_color(thumb_hover and ui.COLOR.SCROLLBAR_THUMB_HOVER or ui.COLOR.SCROLLBAR_THUMB)
        ui.rect(scrollbar_x, thumb_y, scrollbar_w, thumb_height, true)
    end
    
    return double_clicked_index
end

function ui.draw_current_effect_name(x, y, w, h, effect_name, efx_mode)
    -- 現在のエフェクト名を大きく表示 (A/Bラベルと色付き)
    if not effect_name then
        return
    end
    
    -- 背景色 (EFX AまたはBに応じて)
    if efx_mode == "B" then
        ui.set_color({92, 45, 45})  -- 赤系
    else
        ui.set_color({45, 92, 45})  -- 緑系
    end
    ui.rect(x, y, w, h, true)
    
    -- ボーダー
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    -- 左側にラベル "A:" または "B:"
    gfx.setfont(1, "Meiryo UI", 20)
    local label = (efx_mode == "B") and "B:" or "A:"
    local label_w, label_h = gfx.measurestr(label)
    ui.set_color({255, 255, 255})  -- 白
    gfx.x = x + 10
    gfx.y = y + (h - label_h) / 2
    gfx.drawstr(label)
    
    -- エフェクト名をラベルの右に表示
    gfx.setfont(1, "Meiryo UI", 24)
    local text_w, text_h = gfx.measurestr(effect_name)
    ui.set_color({255, 255, 255})  -- 白
    gfx.x = x + 10 + label_w + 10
    gfx.y = y + (h - text_h) / 2
    gfx.drawstr(effect_name)
    
    -- フォントを元に戻す
    gfx.setfont(1, "Meiryo UI", 14)
end

function ui.draw_preset_panel(x, y, w, h, effect_no)
    -- 背景パネル
    ui.set_color(ui.COLOR.PANEL)
    ui.rect(x, y, w, h, true)

    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)

    local eff_key = tostring(effect_no)
    local eff_presets = (app.presets and app.presets[eff_key]) or {}

    local btn_w = ui.EFX_BUTTON_WIDTH   -- 50
    local btn_h = ui.EFX_BUTTON_HEIGHT  -- 25
    local spacing = ui.EFX_BUTTON_SPACING -- 5
    local start_x = x + 10
    local btn_y = y + (h - btn_h) / 2

    local is_set_mode = ui.state.preset_set_mode
    gfx.setfont(1, "Meiryo UI", 14)

    -- 1. "Set" ボタン
    local set_btn_x = start_x
    local set_hover = ui.is_mouse_over(set_btn_x, btn_y, btn_w, btn_h)

    if is_set_mode then
        -- SETモード待機中: 強調表示（鮮やかな黄色、黒文字）
        ui.set_color(set_hover and {255, 230, 80} or {220, 190, 30})
        ui.rect(set_btn_x, btn_y, btn_w, btn_h, true)
        ui.set_color({255, 255, 140})
        ui.rect(set_btn_x, btn_y, btn_w, btn_h, false)
        local lbl_w = gfx.measurestr("Set")
        ui.text(set_btn_x + (btn_w - lbl_w) / 2, btn_y + btn_h / 2 - 7, "Set", {20, 20, 20})
    else
        -- 通常時
        ui.set_color(set_hover and ui.COLOR.BUTTON_HOVER or ui.COLOR.BUTTON)
        ui.rect(set_btn_x, btn_y, btn_w, btn_h, true)
        ui.set_color(ui.COLOR.BORDER)
        ui.rect(set_btn_x, btn_y, btn_w, btn_h, false)
        local lbl_w = gfx.measurestr("Set")
        ui.text(set_btn_x + (btn_w - lbl_w) / 2, btn_y + btn_h / 2 - 7, "Set", ui.COLOR.TEXT)
    end

    if set_hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
        ui.state.preset_set_mode = not ui.state.preset_set_mode
    end

    -- 2. "1" 〜 "10" ボタン
    for slot = 1, 10 do
        local slot_key = tostring(slot)
        local has_preset = (eff_presets[slot_key] ~= nil)
        local is_active = (ui.state.last_used_preset == slot and not is_set_mode)
        local btn_x = set_btn_x + slot * (btn_w + spacing)
        local hover = ui.is_mouse_over(btn_x, btn_y, btn_w, btn_h)

        if is_set_mode then
            -- SET待機中: すべての1-10ボタンを黄色にして待機！
            ui.set_color(hover and {255, 230, 80} or {220, 190, 30})
            ui.rect(btn_x, btn_y, btn_w, btn_h, true)
            ui.set_color({255, 255, 120})
            ui.rect(btn_x, btn_y, btn_w, btn_h, false)

            local label = tostring(slot)
            local lbl_w = gfx.measurestr(label)
            ui.text(btn_x + (btn_w - lbl_w) / 2, btn_y + btn_h / 2 - 7, label, {20, 20, 20})

            if hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
                -- プリセット登録！
                save_preset_slot(effect_no, slot)
                ui.state.preset_set_mode = false
            end
        else
            -- 通常時: 呼び出し
            if has_preset then
                -- 登録済みスロット: スレートブルー系（存在がわかる色）
                ui.set_color(hover and {75, 100, 135} or {55, 75, 105})
                ui.rect(btn_x, btn_y, btn_w, btn_h, true)
                if is_active then
                    ui.set_color({80, 220, 240}) -- 直近で呼び出し/保存したアクティブ枠線
                else
                    ui.set_color(hover and {100, 135, 180} or {80, 110, 140})
                end
                ui.rect(btn_x, btn_y, btn_w, btn_h, false)

                local label = tostring(slot)
                local lbl_w = gfx.measurestr(label)
                ui.text(btn_x + (btn_w - lbl_w) / 2, btn_y + btn_h / 2 - 7, label, {240, 240, 240})
            else
                -- 未登録スロット: 暗い灰色
                ui.set_color(hover and {55, 55, 55} or {40, 40, 40})
                ui.rect(btn_x, btn_y, btn_w, btn_h, true)
                ui.set_color({60, 60, 60})
                ui.rect(btn_x, btn_y, btn_w, btn_h, false)

                local label = tostring(slot)
                local lbl_w = gfx.measurestr(label)
                ui.text(btn_x + (btn_w - lbl_w) / 2, btn_y + btn_h / 2 - 7, label, {110, 110, 110})
            end

            if hover and gfx.mouse_cap & 1 == 1 and not ui.state.mouse_down_last then
                if has_preset then
                    load_preset_slot(effect_no, slot)
                end
            end
        end
    end
end

function ui.draw_rdc_panel(x, y, w, h, reverb, delay, chorus)
    -- パネル背景
    ui.set_color(ui.COLOR.PANEL)
    ui.rect(x, y, w, h, true)
    
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)
    
    -- タイトル
    ui.set_color(ui.COLOR.TEXT)
    ui.text(x + 5, y + 5, "Send Levels:", ui.COLOR.TEXT)
    
    local slider_y = y + 25
    local slider_h = 20
    local slider_w = (w - 40) / 3
    local spacing = 10
    
    local updated_values = {}
    
    -- Reverb
    local reverb_x = x + 10
    ui.set_color(ui.COLOR.TEXT_DIM)
    ui.text(reverb_x, slider_y - 15, "Reverb", ui.COLOR.TEXT_DIM)
    local new_reverb = ui.draw_simple_slider(reverb_x, slider_y, slider_w, slider_h, reverb, 0, 127, "reverb")
    if new_reverb ~= reverb then
        updated_values.reverb = new_reverb
    end
    
    -- Delay
    local chorus_x = reverb_x + slider_w + spacing
    ui.set_color(ui.COLOR.TEXT_DIM)
    ui.text(chorus_x, slider_y - 15, "Delay", ui.COLOR.TEXT_DIM)
    local new_chorus = ui.draw_simple_slider(chorus_x, slider_y, slider_w, slider_h, chorus, 0, 127, "chorus")
    if new_chorus ~= chorus then
        updated_values.chorus = new_chorus
    end
    
    -- Chorus
    local delay_x = chorus_x + slider_w + spacing
    ui.set_color(ui.COLOR.TEXT_DIM)
    ui.text(delay_x, slider_y - 15, "Chorus", ui.COLOR.TEXT_DIM)
    local new_delay = ui.draw_simple_slider(delay_x, slider_y, slider_w, slider_h, delay, 0, 127, "delay")
    if new_delay ~= delay then
        updated_values.delay = new_delay
    end
    
    return updated_values
end

-- 送りのレベル（0〜127）のスライダー。動きはほかのつまみと同じ（ui.knob_input）
-- Ctrl+クリックの既定：GS のインサーション EFX の送り（リバーブへ 40、ほかは 0）
local SEND_DEFAULT = {reverb = 40, chorus = 0, delay = 0}
function ui.draw_simple_slider(x, y, w, h, value, min_val, max_val, id)
    local hover = ui.is_mouse_over(x, y, w, h)
    value = ui.knob_input(id, x, y, w, h, value, min_val, max_val,
        function() return SEND_DEFAULT[id] or 0 end,
        function()
            return ui.knob_ask(string.format("送りのレベル（%d〜%d）", min_val, max_val), tostring(value), function(s)
                local v = tonumber(s)
                return v and math.floor(v + 0.5)
            end)
        end)

    -- 背景
    ui.set_color(ui.COLOR.SLIDER_BG)
    ui.rect(x, y, w, h, true)

    -- 埋められた部分
    local fill_w = w * (value - min_val) / (max_val - min_val)

    -- view_modeに応じた色を選択（EFX A=緑、EFX B=赤、SysEff=青）
    local fill_color, hover_color
    if ui.state.view_mode == "efx_a" then
        fill_color = ui.COLOR.SLIDER_FILL_EFX_A
        hover_color = ui.COLOR.SLIDER_HOVER_EFX_A
    elseif ui.state.view_mode == "efx_b" then
        fill_color = ui.COLOR.SLIDER_FILL_EFX_B
        hover_color = ui.COLOR.SLIDER_HOVER_EFX_B
    else
        fill_color = ui.COLOR.SLIDER_FILL
        hover_color = ui.COLOR.SLIDER_HOVER
    end

    local selected_color = (hover or ui.state.dragging_slider == id) and hover_color or fill_color
    ui.set_color(selected_color)
    ui.rect(x, y, fill_w, h, true)

    -- 枠線
    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)

    -- 値表示
    local value_text = string.format("%d", value)
    local text_w, text_h = gfx.measurestr(value_text)
    ui.set_color(ui.COLOR.TEXT)
    ui.text(x + (w - text_w) / 2, y + (h - text_h) / 2, value_text, ui.COLOR.TEXT)

    return value
end

function ui.draw_parameter_row(x, y, w, parameter, value)
    local row_h = ui.PARAMETER_ROW_HEIGHT
    
    local bg_alpha = 0.1
    ui.set_color(ui.COLOR.BUTTON, bg_alpha)
    ui.rect(x, y, w, row_h, true)
    
    -- ホバー検出
    local is_hovering = ui.is_mouse_over(x, y, w, row_h)
    if is_hovering then
        ui.state.hover_parameter = parameter
    end
    
    local offset_x = x
    
    -- Get address from JSON structure
    local addr = parameter.address or {}
    local sub = addr["sub"] or "00"
    local offset = addr["offset"] or "00"
    
    local addr_text = string.format("%s %s", sub, offset)
    
    -- MIDIイベントに存在するかチェック（offsetの16進数文字列で直接比較）
    local params = get_current_parameters()
    local found_in_midi = false
    for _, p in ipairs(params) do
        -- p.param_idは"03_0C"や"04_0C"の形式
        -- offsetのみで比較（subは現在のEFX A/Bで異なる）
        local _, p_offset = string.match(p.param_id, "(%x%x)_(%x%x)")
        if p_offset and string.upper(p_offset) == string.upper(offset) then
            found_in_midi = true
            break
        end
    end
    
    ui.text(offset_x + 5, y + 8, addr_text, ui.COLOR.TEXT_DIM)
    offset_x = offset_x + ui.ADDRESS_WIDTH
    
    ui.text(offset_x + 5, y + 8, parameter.name, ui.COLOR.TEXT)
    offset_x = offset_x + ui.NAME_WIDTH
    
    local slider_x = offset_x + 5
    local slider_y = y + 5
    local slider_w = ui.SLIDER_WIDTH
    local slider_h = 20
    
    -- param_idを生成（スライダー操作用）
    local sub_num = tonumber(sub, 16) or 0
    local offset_num = tonumber(offset, 16) or 0
    local param_id = string.format("%02X_%02X", sub_num, offset_num)
    
    -- パラメーター型に応じた範囲を決定
    local min_val = 0
    local max_val = 127
    local type_info = parameter.type_info
    
    -- conversion_tableまたはテーブル型がある場合
    if parameter.conversion_table or (type_info and type_info.type == "table") then
        if type_info and type_info.min and type_info.max then
            min_val = type_info.min
            max_val = type_info.max
        else
            min_val = 0
            max_val = 127
        end
    elseif parameter.conversion_column then
        min_val = 0
        max_val = 127
    elseif type_info and type_info.type == "discrete" then
        -- 離散値：選択肢の数-1が最大値
        if type_info.choices then
            max_val = #type_info.choices - 1
        end
        if is_amp_type_small_disabled(parameter) then
            min_val = 1
            if value < 1 then value = 1 end
        end
    elseif type_info and type_info.type == "numeric_range" then
        -- 整数範囲の場合、段階数に応じてフェーダー範囲を設定
        local param_min = type_info.min or 0
        local param_max = type_info.max or 127
        
        -- data_rangeがある場合は、そのMIDI範囲をそのまま使用
        if type_info.data_range then
            local min_str, max_str = string.match(type_info.data_range, "(%x+)%s*%-%s*(%x+)")
            if min_str and max_str then
                local midi_min = tonumber(min_str, 16) or 0
                local midi_max = tonumber(max_str, 16) or 127
                min_val = midi_min
                max_val = midi_max
            end
        -- data_rangeがない場合、整数かどうかチェックして段階数を決定
        elseif param_min == math.floor(param_min) and param_max == math.floor(param_max) then
            local range = param_max - param_min
            -- 範囲が小さい場合（例：-12～+12 = 25段階）、その段階数に合わせる
            if range <= 127 and not (parameter.name and string.find(parameter.name, "Rate")) then
                min_val = 0
                max_val = range
            end
        end
    end
    
    local new_value = ui.draw_slider(slider_x, slider_y, slider_w, slider_h, 
        value, min_val, max_val, param_id, parameter)
    
    offset_x = offset_x + ui.SLIDER_WIDTH + 10
    
    local display_value = value_converter.midi_to_display(parameter, value)
    
    -- MIDIイベントに存在しない場合は表示値に「!」を付ける
    if not found_in_midi then
        display_value = display_value .. " !"
    end
    
    ui.text(offset_x + 5, y + 8, display_value, ui.COLOR.TEXT)
    
    return param_id, new_value
end

-- ---------------------------------------------------------------- つまみの動き（ミキサーと同じ）
-- 押して 3 ピクセル動かしてから、左右のドラッグで値を変える（押した所へ飛ばない）。1 ピクセルあたり、ふつうはスライダーの幅で
-- 端から端まで（短いものは 127 ピクセルで端から端）、Shift を押している間は 0〜127 の 0.15。
-- ホイールで 1 つずつ（Ctrl で 5）。ダブルクリックで数字を入れる。Ctrl+クリックで既定の値。
-- つまんでいる間・ホイールを回している間は書かずに試聴だけ。離したとき（ホイールは止めて 0.6 秒）に、最後の値を1回だけ書く
ui.KNOB_WHEEL_COMMIT_S = 0.6

-- ダブルクリックか（0.4 秒以内に同じものを2回押した）
function ui.knob_double_click(id)
    local now = reaper.time_precise()
    if ui.state.knob_last_id == id and now - (ui.state.knob_last_t or 0) < 0.4 then
        ui.state.knob_last_id = nil
        return true
    end
    ui.state.knob_last_id, ui.state.knob_last_t = id, now
    return false
end

-- 数字を入れてもらう。label は今の表示、parse(文字列) → 新しい値（nil なら変えない）
function ui.knob_ask(label, current_text, parse)
    local ok, s = reaper.GetUserInputs("X-Laben EFX", 1, label .. ",extrawidth=80", current_text)
    if ok and s ~= "" then return parse(s) end
end

-- 1 つのつまみの、押す・ドラッグ・ホイール。pos は今の位置（min_val〜max_val）。新しい位置を返す（変わらなければ pos）
-- on_ctrl()：Ctrl+クリックの位置、on_double()：ダブルクリックの位置（nil なら変えない）
function ui.knob_input(id, x, y, w, h, pos, min_val, max_val, on_ctrl, on_double)
    local hover = ui.is_mouse_over(x, y, w, h)
    local down = gfx.mouse_cap & 1 == 1
    local d = ui.state.knob_drag
    if hover and down and not ui.state.mouse_down_last and not d then
        if gfx.mouse_cap & 4 == 4 then
            local v = on_ctrl()
            if v ~= nil then ui.state.knob_commit = true; return v end
            return pos
        end
        if ui.knob_double_click(id) then
            local v = on_double()
            if v ~= nil then ui.state.knob_commit = true; return math.max(min_val, math.min(max_val, v)) end
            return pos
        end
        ui.state.knob_drag = {id = id, start = pos, pos = pos, mx = gfx.mouse_x, moved = false, acc = 0}
        ui.state.dragging_slider = id
        midi_utils.begin_preview()
        d = ui.state.knob_drag
    end
    if d and d.id == id then
        if not d.moved and math.abs(gfx.mouse_x - d.mx) >= 3 then d.moved, d.last = true, d.mx end
        if d.moved then
            local range = max_val - min_val
            local step = (gfx.mouse_cap & 8 == 8) and (0.15 * range / 127) or (range / math.max(w, 127))
            d.acc = d.acc + (gfx.mouse_x - d.last) * step
            d.last = gfx.mouse_x
            d.pos = math.max(min_val, math.min(max_val, math.floor(d.start + d.acc + 0.5)))
        end
        if not down then
            ui.state.knob_drag = nil
            ui.state.dragging_slider = nil
            ui.state.knob_commit = true
        end
        return d.pos
    end
    if hover and not d and gfx.mouse_wheel ~= 0 then
        local step = (gfx.mouse_cap & 4 == 4) and 5 or 1
        local v = math.max(min_val, math.min(max_val, pos + (gfx.mouse_wheel > 0 and step or -step)))
        gfx.mouse_wheel = 0
        midi_utils.begin_preview()
        ui.state.knob_wheel_at = reaper.time_precise()
        return v
    end
    return pos
end

-- 1コマの終わりに：離した・ホイールを止めて少したったら、試聴していた値を書く
function ui.knob_flush()
    local now = reaper.time_precise()
    local wheel_done = ui.state.knob_wheel_at and now - ui.state.knob_wheel_at > ui.KNOB_WHEEL_COMMIT_S
    if (ui.state.knob_commit or wheel_done) and not ui.state.knob_drag then
        ui.state.knob_commit, ui.state.knob_wheel_at = false, nil
        midi_utils.end_preview()
    end
end

function ui.draw_slider(x, y, w, h, value, min_val, max_val, slider_id, parameter)
    local hover = ui.is_mouse_over(x, y, w, h)

    -- MIDI の値とスライダーの位置の対応（段階式のフェーダー：-12〜+12 なら位置 0〜24 が MIDI 52〜76、64 が真ん中）
    local shifted = 0
    if parameter and parameter.type_info and parameter.type_info.type == "numeric_range" and not parameter.type_info.data_range then
        local param_min = parameter.type_info.min or 0
        local param_max = parameter.type_info.max or 127
        if param_min == math.floor(param_min) and param_max == math.floor(param_max) then
            local range = param_max - param_min
            if range <= 127 and max_val == range and param_min ~= 0 and not (parameter.name and string.find(parameter.name, "Rate")) then
                shifted = 64 - range / 2
            end
        end
    end
    local function to_pos(v) return v - shifted end
    local function to_midi(p)
        local v = math.floor(p + shifted + 0.5)
        if shifted ~= 0 then v = math.max(0, math.min(127, v)) end
        return v
    end

    local start_pos = math.max(min_val, math.min(max_val, to_pos(value)))
    local pos = ui.knob_input(slider_id, x, y, w, h, start_pos, min_val, max_val,
        function()
            if parameter and parameter.type_info then return to_pos(param_default_value(parameter)) end
            return to_pos(parameter and parameter.default or 64)
        end,
        function()
            local shown = parameter and value_converter.midi_to_display(parameter, value) or tostring(to_pos(value))
            local name = parameter and parameter.name or "値"
            return ui.knob_ask(string.format("%s（今：%s）", name, shown), shown, function(s)
                if parameter and parameter.type_info then return to_pos(value_converter.display_to_midi(parameter, s)) end
                return tonumber(s)
            end)
        end)
    -- 動かしていなければ、元の値のまま返す（範囲の外の値を、表示のために丸めて書いてしまわないように）
    local new_value = (pos == start_pos) and value or to_midi(pos)
    local is_dragging = (ui.state.dragging_slider == slider_id)

    ui.set_color(ui.COLOR.SLIDER_BG)
    ui.rect(x, y, w, h, true)

    local fill_width = (pos - min_val) / math.max(1, max_val - min_val) * w

    -- view_modeに応じた色を選択（EFX A=緑、EFX B=赤、SysEff=青）
    local fill_color, hover_color
    if ui.state.view_mode == "efx_a" then
        fill_color = ui.COLOR.SLIDER_FILL_EFX_A
        hover_color = ui.COLOR.SLIDER_HOVER_EFX_A
    elseif ui.state.view_mode == "efx_b" then
        fill_color = ui.COLOR.SLIDER_FILL_EFX_B
        hover_color = ui.COLOR.SLIDER_HOVER_EFX_B
    else
        fill_color = ui.COLOR.SLIDER_FILL
        hover_color = ui.COLOR.SLIDER_HOVER
    end

    ui.set_color((hover or is_dragging) and hover_color or fill_color)
    ui.rect(x, y, fill_width, h, true)

    ui.set_color(ui.COLOR.BORDER)
    ui.rect(x, y, w, h, false)

    return new_value
end

function ui.draw_parameter_list(x, y, w, h, parameters, values)
    local visible_count = math.floor(h / ui.PARAMETER_ROW_HEIGHT)
    local scroll = ui.state.parameter_scroll
    local max_scroll = math.max(0, #parameters - visible_count)
    
    -- スクロールバーエリアの判定用
    local scrollbar_w = 8
    local scrollbar_x = x + w - scrollbar_w - 2
    local is_in_scrollbar = ui.is_mouse_over(scrollbar_x, y, scrollbar_w + 2, h)
    
    local updated_values = {}
    
    for i = scroll + 1, math.min(scroll + visible_count, #parameters) do
        local param = parameters[i]
        
        -- Generate param_id from address
        local addr = param.address or {}
        local sub = tonumber(addr["sub"], 16) or 0
        local offset = tonumber(addr["offset"], 16) or 0
        local param_id = string.format("%02X_%02X", sub, offset)
        
        local value = values[param_id] or 0
        local row_y = y + (i - scroll - 1) * ui.PARAMETER_ROW_HEIGHT
        
        -- スクロールバードラッグ中はパラメーター操作をスキップ
        local returned_param_id, new_value
        if not ui.state.dragging_param_scrollbar and not is_in_scrollbar then
            returned_param_id, new_value = ui.draw_parameter_row(x, row_y, w, param, value)
        else
            -- ドラッグ中は表示のみ
            ui.draw_parameter_row(x, row_y, w, param, value)
            new_value = value
        end
        
        if new_value ~= value then
            updated_values[returned_param_id] = new_value
        end
    end
    
    if ui.is_mouse_over(x, y, w, h) then
        local scroll_delta = gfx.mouse_wheel
        if scroll_delta ~= 0 then
            ui.state.parameter_scroll = math.max(0, math.min(#parameters - visible_count, 
                ui.state.parameter_scroll - scroll_delta))
            gfx.mouse_wheel = 0
        end
    end
    
    -- スクロールバー
    if max_scroll > 0 then
        local scrollbar_w = 8
        local scrollbar_x = x + w - scrollbar_w - 2
        local scrollbar_y = y + 2
        local scrollbar_h = h - 4
        
        -- スクロールバー背景
        ui.set_color(ui.COLOR.SCROLLBAR_BG)
        ui.rect(scrollbar_x, scrollbar_y, scrollbar_w, scrollbar_h, true)
        
        -- スクロールバーつまみ
        local thumb_height = math.max(20, scrollbar_h * visible_count / #parameters)
        local thumb_y = scrollbar_y + (scrollbar_h - thumb_height) * (scroll / max_scroll)
        
        local thumb_hover = ui.is_mouse_over(scrollbar_x, thumb_y, scrollbar_w, thumb_height)
        local mouse_down = gfx.mouse_cap & 1 == 1
        
        -- ドラッグ開始
        if thumb_hover and mouse_down and not ui.state.mouse_down_last then
            ui.state.dragging_param_scrollbar = true
        end
        
        -- ドラッグ中
        if ui.state.dragging_param_scrollbar and mouse_down then
            local mouse_y = gfx.mouse_y
            local relative_y = mouse_y - scrollbar_y - thumb_height / 2
            local scroll_ratio = relative_y / (scrollbar_h - thumb_height)
            scroll_ratio = math.max(0, math.min(1, scroll_ratio))
            ui.state.parameter_scroll = math.floor(scroll_ratio * max_scroll + 0.5)
        end
        
        -- ドラッグ終了
        if not mouse_down then
            ui.state.dragging_param_scrollbar = false
        end
        
        ui.set_color(thumb_hover and ui.COLOR.SCROLLBAR_THUMB_HOVER or ui.COLOR.SCROLLBAR_THUMB)
        ui.rect(scrollbar_x, thumb_y, scrollbar_w, thumb_height, true)
    end
    
    return updated_values
end

function ui.draw_help_box(x, y, w, h, hover_effect, hover_parameter)
    -- 枠線を描画
    ui.set_color(ui.COLOR.BORDER)
    gfx.rect(x, y, w, h, 0)
    
    -- 背景を描画
    ui.set_color(ui.COLOR.PANEL)
    gfx.rect(x + 1, y + 1, w - 2, h - 2, 1)
    
    -- テキストを表示
    local text = ""
    local title = ""
    
    if hover_parameter then
        title = hover_parameter.name or ""
        text = hover_parameter.description or ""
    elseif hover_effect then
        title = hover_effect.name or ""
        text = hover_effect.description or ""
    end
    
    if text ~= "" then
        ui.set_color(ui.COLOR.TEXT)
        gfx.setfont(1, "Meiryo UI", 16)
        
        -- タイトル表示
        if title ~= "" then
            gfx.x = x + 8
            gfx.y = y + 6
            gfx.drawstr(title)
            y = y + 22
            h = h - 22
        end
        
        -- 説明テキストを折り返して表示
        gfx.setfont(1, "Meiryo UI", 14)
        local max_width = w - 16
        
        -- まず改行文字で分割
        local paragraphs = {}
        for paragraph in text:gmatch("[^\r\n]+") do
            table.insert(paragraphs, paragraph)
        end
        if #paragraphs == 0 then
            paragraphs = {text}
        end
        
        local lines = {}
        
        for _, paragraph in ipairs(paragraphs) do
            -- 段落が空の場合はスキップ
            if paragraph:match("%S") then
                local current_line = ""
                
                -- 文字単位で処理（日本語対応）
                local byte_index = 1
                while byte_index <= #paragraph do
                    -- UTF-8の文字を取得
                    local char_byte = paragraph:byte(byte_index)
                    local char_len = 1
                    if char_byte >= 0xF0 then
                        char_len = 4
                    elseif char_byte >= 0xE0 then
                        char_len = 3
                    elseif char_byte >= 0xC0 then
                        char_len = 2
                    end
                    
                    local char = paragraph:sub(byte_index, byte_index + char_len - 1)
                    local test_line = current_line .. char
                    local str_w, str_h = gfx.measurestr(test_line)
                    
                    if str_w <= max_width then
                        current_line = test_line
                    else
                        if current_line ~= "" then
                            table.insert(lines, current_line)
                        end
                        current_line = char
                    end
                    
                    byte_index = byte_index + char_len
                end
                
                if current_line ~= "" then
                    table.insert(lines, current_line)
                end
            end
        end
        
        -- 行を表示
        gfx.x = x + 8
        gfx.y = y + 6
        for i, line in ipairs(lines) do
            if gfx.y + 15 > y + h then
                break
            end
            gfx.x = x + 8
            gfx.drawstr(line)
            gfx.y = gfx.y + 15
        end
    end
end

function ui.update_mouse_state()
    ui.state.mouse_down_last = (gfx.mouse_cap & 1 == 1)
end

function ui.is_open()
    if not ui.state.window_open then return false end
    local c = gfx.getchar()
    ui.state.last_char = c
    if c >= 0 and ok_win then win.track() end
    return c >= 0
end

function ui.clear_background()
    ui.set_color(ui.COLOR.BACKGROUND)
    gfx.rect(0, 0, gfx.w, gfx.h, 1)
    -- 上の帯（EFX の割り当てのボタンの列）：ミキサー・ドラムの上の帯と同じく、少しだけ明るくして下に線（色は EFXUI のまま）
    local hh = ui.PADDING + ui.EFX_BUTTONS_HEIGHT + ui.PADDING / 2
    local c = ui.COLOR.PANEL
    gfx.gradrect(0, 0, gfx.w, hh, c[1] / 255, c[2] / 255, c[3] / 255, 1, 0, 0, 0, 0, -0.0004, -0.0004, -0.0003, 0)
    ui.set_color(ui.COLOR.BORDER, 0.6)
    gfx.line(0, hh - 1, gfx.w, hh - 1)
end

function ui.begin_frame()
    -- 大きさ：今のウィンドウに合わせる（下の帯の分をあける）。狭すぎるときは最小の大きさで並べる（はみ出た所は見えない）
    ui.WINDOW_WIDTH = math.max(ui.MIN_WIDTH, gfx.w)
    ui.WINDOW_HEIGHT = math.max(ui.MIN_HEIGHT, gfx.h - ui.STATUS_HEIGHT)
    ui.clear_background()
end

-- 下の帯：知らせ（書けなかったなど）
function ui.draw_status()
    local y = gfx.h - ui.STATUS_HEIGHT
    ui.set_color(ui.COLOR.STATUS_BG)
    ui.rect(0, y, gfx.w, ui.STATUS_HEIGHT, true)
    local now = reaper.time_precise()
    if app.status and now < (app.status_until or 0) then
        gfx.setfont(1, "Meiryo UI", 14)
        ui.text(10, y + 5, app.status, app.status_warn and ui.COLOR.WARN or ui.COLOR.TEXT_DIM)
    else
        gfx.setfont(1, "Meiryo UI", 13)
        ui.text(10, y + 5, "右クリック：ドッキング・大きさ・読み直し・調べもの表示", ui.COLOR.TEXT_DISABLED)
    end
end

function ui.end_frame()
    gfx.update()
    ui.update_mouse_state()
end

-- ============================================================================
-- Application
-- ============================================================================
local app = {
    database = nil,
    effects = {},
    selected_effect_index = nil,
    current_part = 0x12,
    current_parameters = {},
    midi_take = nil,
    midi_track = nil,       -- MIDIトラックの参照
    efx_on_states = {},
    -- RDC levels for EFX A (sub 03)
    reverb_level_a = 0,     -- Reverb A: 0-127
    delay_level_a = 0,      -- Delay A: 0-127
    chorus_level_a = 0,     -- Chorus A: 0-127
    -- RDC levels for EFX B (sub 04)
    reverb_level_b = 0,     -- Reverb B: 0-127
    delay_level_b = 0,      -- Delay B: 0-127
    chorus_level_b = 0,     -- Chorus B: 0-127
    temporary_events = {},  -- 一時的に挿入されたアイテムを追跡
    playback_insert_ahead_sec = 0.45, -- 再生中の一時イベント挿入先行量（450ms・高レイテンシ対策）
    is_playing = false,     -- 現在再生中かどうか
    last_play_state = 0,    -- 前回の再生状態
    -- System Effects
    system_effects = nil,   -- システムエフェクトデータベース
    system_effect_values = {}, -- システムエフェクトの現在値
    -- EFX A/B Support
    current_efx = "A",      -- "A" or "B" - 現在選択中のEFX (subアドレス切り替えのみ)
    efx_a_parameters = {},     -- EFX A のパラメータ配列
    efx_b_parameters = {},     -- EFX B のパラメータ配列
    presets = {}               -- EFXプリセット
}

function load_presets()
    local script_path = debug.getinfo(1, "S").source:match("@?(.*[/\\])") or ""
    local preset_path = script_path .. "xlaben_efx_presets.json"
    local presets, err = json.load_file(preset_path)
    if presets and type(presets) == "table" then
        app.presets = presets
        reaper.ShowConsoleMsg("[PRESET] Loaded EFX presets database\n")
    else
        app.presets = {}
    end
end

function save_presets()
    local script_path = debug.getinfo(1, "S").source:match("@?(.*[/\\])") or ""
    local preset_path = script_path .. "xlaben_efx_presets.json"
    json.save_file(preset_path, app.presets)
end

function save_preset_slot(effect_no, slot)
    local effect = get_current_effect()
    if not effect then return end

    local param_vals = get_parameter_values()
    local rdc = get_current_rdc_levels()

    local eff_key = tostring(effect_no)
    if not app.presets then app.presets = {} end
    if not app.presets[eff_key] then
        app.presets[eff_key] = {}
    end

    app.presets[eff_key][tostring(slot)] = {
        parameters = param_vals,
        send_levels = {
            reverb = rdc.reverb,
            delay = rdc.delay,
            chorus = rdc.chorus
        }
    }

    save_presets()
    ui.state.last_used_preset = slot
    reaper.ShowConsoleMsg(string.format("[PRESET] Saved preset slot %d for EFX %s (%s)\n", slot, eff_key, effect.name or ""))
end

function load_preset_slot(effect_no, slot)
    local eff_key = tostring(effect_no)
    if not app.presets then return end
    local eff_presets = app.presets[eff_key]
    if not eff_presets then return end

    local preset = eff_presets[tostring(slot)]
    if not preset then return end

    -- パラメータ更新
    if preset.parameters then
        for param_id, val in pairs(preset.parameters) do
            update_parameter(param_id, val)
        end
    end

    -- Send Levels更新
    if preset.send_levels then
        if preset.send_levels.reverb ~= nil then
            update_rdc_level("reverb", preset.send_levels.reverb)
        end
        if preset.send_levels.delay ~= nil then
            update_rdc_level("delay", preset.send_levels.delay)
        end
        if preset.send_levels.chorus ~= nil then
            update_rdc_level("chorus", preset.send_levels.chorus)
        end
    end

    ui.state.last_used_preset = slot
    reaper.ShowConsoleMsg(string.format("[PRESET] Loaded preset slot %d for EFX %s\n", slot, eff_key))
end

function save_syseff_preset_slot(section_name, slot)
    local section_data = app.system_effects and app.system_effects.sections and app.system_effects.sections[section_name]
    if not section_data then return end

    local param_vals = {}
    for _, param in ipairs(section_data.parameters) do
        local addr = param.address
        local sub = tonumber(addr.sub, 16) or 0
        local offset = tonumber(addr.offset, 16) or 0
        local param_id = string.format("%s_%02X_%02X", section_name, sub, offset)
        local val = app.system_effect_values[param_id] or tonumber(param.default_hex, 16) or 0
        param_vals[param_id] = val
    end

    local key = "syseff_" .. section_name
    if not app.presets then app.presets = {} end
    if not app.presets[key] then app.presets[key] = {} end

    app.presets[key][tostring(slot)] = {
        parameters = param_vals
    }

    save_presets()
    if not ui.state.syseff_last_used_preset then ui.state.syseff_last_used_preset = {} end
    ui.state.syseff_last_used_preset[section_name] = slot
    reaper.ShowConsoleMsg(string.format("[PRESET] Saved SysEff %s preset slot %d\n", section_name, slot))
end

function load_syseff_preset_slot(section_name, slot)
    local key = "syseff_" .. section_name
    if not app.presets then return end
    local eff_presets = app.presets[key]
    if not eff_presets then return end

    local preset = eff_presets[tostring(slot)]
    if not preset or not preset.parameters then return end

    local section_data = app.system_effects and app.system_effects.sections and app.system_effects.sections[section_name]
    if not section_data then return end

    for _, param in ipairs(section_data.parameters) do
        local addr = param.address
        local sub = tonumber(addr.sub, 16) or 0
        local offset = tonumber(addr.offset, 16) or 0
        local param_id = string.format("%s_%02X_%02X", section_name, sub, offset)
        local val = preset.parameters[param_id]
        if val ~= nil then
            write_system_effect_parameter(section_name, param, val)
        end
    end

    if not ui.state.syseff_last_used_preset then ui.state.syseff_last_used_preset = {} end
    ui.state.syseff_last_used_preset[section_name] = slot
    if section_name == "delay" then
        ui.state.delay_sync_note_idx = nil
    end
    reaper.ShowConsoleMsg(string.format("[PRESET] Loaded SysEff %s preset slot %d\n", section_name, slot))
end

function load_database()
    local script_path = debug.getinfo(1, "S").source:match("@?(.*[/\\])") or ""
    local db_path = script_path .. "xlaben_efx_data.json"
    -- 一度読んだ結果を cache/ に Lua のファイルとして置き、次からはそれを読む（開く・切り替えるのが速い。lib/efx_json.lua）
    reaper.RecursiveCreateDirectory(script_path .. "cache", 0)
    local db, err = json.load_cached(db_path, script_path .. "cache/xlaben_efx_data.lua")
    
    if not db then
        reaper.ShowMessageBox("Failed to load database: " .. (err or "unknown error"), 
            "Error", 0)
        return false
    end
    
    app.database = db
    app.effects = db.effects or {}
    
    -- Load system effects database
    local syseff_path = script_path .. "xlaben_system_effects.json"
    local syseff_db, syseff_err = json.load_cached(syseff_path, script_path .. "cache/xlaben_system_effects.lua")
    
    if syseff_db then
        app.system_effects = syseff_db
        reaper.ShowConsoleMsg("Loaded system effects database\n")
    else
        reaper.ShowConsoleMsg("Warning: System effects database not found\n")
    end
    
    -- appをグローバルに公開してvalue_converterからアクセス可能にする
    load_presets()
    _G.app = app
    
    -- New data available in the database:
    -- db.conversion_table: Value conversion mappings for parameters (0-127)
    --   Each entry maps MIDI value to actual parameter values for different types:
    --   - 1_Pre_Delay_Time_ms: Pre-delay time in milliseconds
    --   - 2_Delay_Time1_ms: Delay time 1 in milliseconds
    --   - 6_Rate1_Hz: Rate 1 in Hz
    --   - 10_EQ_Freq_Hz: EQ frequency in Hz
    --   - etc.
    --   Example: db.conversion_table["0"]["10_EQ_Freq_Hz"] = "200"
    --
    -- db.parameter_usage: Which effects use each parameter type
    --   Example: db.parameter_usage["Pre Delay Time"] = {"Stereo Flanger", "Step Flanger", ...}
    
    reaper.ShowConsoleMsg(string.format("Loaded %d effects from database\n", #app.effects))
    
    if db.conversion_table then
        reaper.ShowConsoleMsg(string.format("Conversion table available with %d value mappings\n", 
            #db.conversion_table))
    end
    
    if db.parameter_usage then
        local param_types = 0
        for _ in pairs(db.parameter_usage) do
            param_types = param_types + 1
        end
        reaper.ShowConsoleMsg(string.format("Parameter usage info available for %d parameter types\n", 
            param_types))
    end
    
    return true
end

function connect_to_track(quiet)
    local take = midi_utils.find_sysex_track()
    app.project = reaper.EnumProjects(-1)

    if not take then
        app.midi_take = nil
        if not quiet then
            reaper.ShowMessageBox("Failed to find X-Laben SysEx in 'SYSTEM_EXCLUSIVE' track.\n" ..
                "Check the Reaper Console for details.",
                "Error", 0)
        end
        return false
    end
    
    app.midi_take = take
    
    -- トラックの参照を保存
    local item = reaper.GetMediaItemTake_Item(take)
    if item then
        app.midi_track = reaper.GetMediaItemTrack(item)
    end
    
    -- EFX A のパラメータを読み込み (Sub 03)
    local params_a = {}
    for _, p in ipairs(midi_utils.read_sysex_parameters(take, app.current_part, 0x40)) do
        if p.sub_address == 0x03 then
            table.insert(params_a, p)
        end
    end
    app.efx_a_parameters = params_a
    app.current_parameters = params_a  -- 後方互換性
    
    -- EFX B のパラメータを読み込み (Sub 04)
    local params_b = {}
    for _, p in ipairs(midi_utils.read_sysex_parameters(take, app.current_part, 0x40)) do
        if p.sub_address == 0x04 then
            table.insert(params_b, p)
        end
    end
    app.efx_b_parameters = params_b
    
    reaper.ShowConsoleMsg(string.format("Connected to track, found %d EFX A parameters, %d EFX B parameters\n", 
        #params_a, #params_b))
    
    -- EFXオン/オフ状態を読み込み
    app.efx_on_states = midi_utils.read_efx_on_states(take, app.current_part)
    
    -- Reverb/Chorus/Delayレベルを読み込み（EFX A: sub 03）
    app.reverb_level_a = midi_utils.read_rdc_level(take, app.current_part, 0x17, 0x03) or 0
    app.chorus_level_a = midi_utils.read_rdc_level(take, app.current_part, 0x18, 0x03) or 0
    app.delay_level_a = midi_utils.read_rdc_level(take, app.current_part, 0x19, 0x03) or 0
    
    -- Reverb/Chorus/Delayレベルを読み込み（EFX B: sub 04）
    app.reverb_level_b = midi_utils.read_rdc_level(take, app.current_part, 0x17, 0x04) or 0
    app.chorus_level_b = midi_utils.read_rdc_level(take, app.current_part, 0x18, 0x04) or 0
    app.delay_level_b = midi_utils.read_rdc_level(take, app.current_part, 0x19, 0x04) or 0
    
    reaper.ShowConsoleMsg(string.format("RDC Levels A: Reverb=%d, Chorus=%d, Delay=%d\n", 
        app.reverb_level_a, app.chorus_level_a, app.delay_level_a))
    reaper.ShowConsoleMsg(string.format("RDC Levels B: Reverb=%d, Chorus=%d, Delay=%d\n", 
        app.reverb_level_b, app.chorus_level_b, app.delay_level_b))
    
    -- Load system effect parameters
    if app.system_effects then
        read_system_effect_parameters()
    end
    
    -- エフェクト検出（A/Bどちらかで検出すれば良い、インデックスは共有）
    app.current_efx = "A"
    detect_current_effect()
    if ui.state.view_mode == "efx_b" then
        app.current_efx = "B"
        detect_current_effect()
    end
    local _, hash = reaper.MIDI_GetHash(take, false, "")
    app.take_hash = hash

    return true
end

-- ============================================================================
-- 知らせ・元に戻す・読み直し
-- ============================================================================
function notify(message, warn, secs)
    app.status = message
    app.status_warn = warn
    app.status_until = reaper.time_precise() + (secs or 6)
end

midi_utils.on_edit = function(label)
    app.pending_undo = app.pending_undo or label
end
midi_utils.on_fail = function(message)
    notify(message, true, 8)
end

-- マウスを離したら、その操作でした書き換えを、元に戻す（Ctrl+Z）の1回分にする
function flush_undo()
    if app.pending_undo and gfx.mouse_cap & 1 == 0 then
        reaper.Undo_OnStateChange("X-Laben EFX：" .. app.pending_undo)
        app.pending_undo = nil
        if app.midi_take then
            local _, hash = reaper.MIDI_GetHash(app.midi_take, false, "")
            app.take_hash = hash
        end
    end
end

-- プロジェクトを替えた・アイテムが消えた・外で MIDI が変わった（手で直した、元に戻した、など）なら読み直す
function refresh_if_changed()
    local now = reaper.time_precise()
    if app.pending_undo or gfx.mouse_cap & 1 == 1 or now - (app.last_check or 0) < 0.5 then return end
    app.last_check = now
    local changed = reaper.EnumProjects(-1) ~= app.project
        or not app.midi_take or not reaper.ValidatePtr2(0, app.midi_take, "MediaItem_Take*")
    if not changed then
        local _, hash = reaper.MIDI_GetHash(app.midi_take, false, "")
        changed = hash ~= app.take_hash
    end
    if changed then
        local had = app.midi_take ~= nil
        if connect_to_track(true) then
            if had then notify("プロジェクトが変わったので読み直しました") end
        else
            notify("このプロジェクトには、X-Laben のセットアップの SysEx（A01 SYSTEM_EXCLUSIVE）が見つかりません", true, 3600)
        end
    end
end

-- 右クリックのメニュー
function context_menu()
    gfx.x, gfx.y = gfx.mouse_x, gfx.mouse_y
    local dock = gfx.dock(-1)
    local sel = gfx.showmenu((dock & 1 == 1 and "!" or "") .. "ドッキング|大きさを戻す|プロジェクトを読み直す||"
        .. (ENABLE_REAPER_LOG and "!" or "") .. "調べもの表示（REAPER のコンソール）")
    if sel == 1 then
        gfx.dock(dock & 1 == 1 and 0 or 1)
    elseif sel == 2 then
        gfx.init("", ui.DEFAULT_WIDTH, ui.DEFAULT_HEIGHT)
    elseif sel == 3 then
        if connect_to_track(true) then notify("読み直しました") else notify("SysEx が見つかりません", true) end
    elseif sel == 4 then
        ENABLE_REAPER_LOG = not ENABLE_REAPER_LOG
        reaper.SetExtState("XLabenEFX", "log", ENABLE_REAPER_LOG and "1" or "0", true)
        notify(ENABLE_REAPER_LOG and "調べもの表示：オン" or "調べもの表示：オフ")
    end
end

function detect_current_effect()
    -- 現在のEFXモード(A or B)に応じて適切なパラメータを検出
    local sub_to_check = get_current_efx_sub()
    local params = get_current_parameters()
    
    if #params == 0 then
        reaper.ShowConsoleMsg("No parameters found in MIDI\n")
        return
    end
    
    -- Debug: show all parameters
    reaper.ShowConsoleMsg(string.format("Total parameters found: %d (checking sub=0x%02X for EFX %s)\n", 
        #params, sub_to_check, app.current_efx))
    for i = 1, math.min(5, #params) do
        local p = params[i]
        reaper.ShowConsoleMsg(string.format("  Param %d: sub=%02X offset=%02X value1=%d (0x%02X) value2=%d (0x%02X)\n", 
            i, p.sub_address, p.offset, p.value1, p.value1, p.value2 or 0, p.value2 or 0))
    end
    
    -- Show all parameters for current sub
    reaper.ShowConsoleMsg(string.format("All sub=0x%02X parameters:\n", sub_to_check))
    for i, p in ipairs(params) do
        if p.sub_address == sub_to_check then
            reaper.ShowConsoleMsg(string.format("  [%d] sub=%02X offset=%02X value1=0x%02X value2=0x%02X\n", 
                i, p.sub_address, p.offset, p.value1, p.value2 or 0))
        end
    end
    
    -- Find effect type from sub=current_efx, offset=0x00 parameter (first effect parameter)
    local efx_type_value1 = nil
    local efx_type_value2 = nil
    
    for _, param in ipairs(params) do
        if param.sub_address == sub_to_check and param.offset == 0x00 then
            efx_type_value1 = param.value1
            efx_type_value2 = param.value2 or 0
            reaper.ShowConsoleMsg(string.format("Found effect type parameter (sub=%02X, offset=00): value1=0x%02X value2=0x%02X\n", 
                sub_to_check, efx_type_value1, efx_type_value2))
            break
        end
    end
    
    -- Match by code field (e.g., "11H, 03H")
    if efx_type_value1 then
        for i, effect in ipairs(app.effects) do
            local code = effect.code or ""
            -- Extract two hex values from code (e.g., "11H" and "03H" from "11H, 03H")
            local hex1, hex2 = string.match(code, "(%x+)H%s*,%s*(%x+)H")
            if hex1 and hex2 then
                local code_value1 = tonumber(hex1, 16)
                local code_value2 = tonumber(hex2, 16)
                -- Match by code pair
                if code_value1 == efx_type_value1 and code_value2 == efx_type_value2 then
                    set_current_effect_index(i)
                    reaper.ShowConsoleMsg(string.format("✓ Detected effect by code: %s (code=%s matches 0x%02X,0x%02X) for EFX %s\n", 
                        effect.name, code, efx_type_value1, efx_type_value2, app.current_efx))
                    return
                end
            elseif hex1 then
                -- Single hex value in code (e.g., "01H")
                local code_value = tonumber(hex1, 16)
                if code_value == efx_type_value1 and efx_type_value2 == 0 then
                    set_current_effect_index(i)
                    reaper.ShowConsoleMsg(string.format("✓ Detected effect by code: %s (code=%s matches 0x%02X) for EFX %s\n", 
                        effect.name, code, efx_type_value1, app.current_efx))
                    return
                end
            end
        end
        reaper.ShowConsoleMsg(string.format("Warning: Effect with code matching 0x%02X,0x%02X not found\n", 
            efx_type_value1, efx_type_value2))
    end
    
    -- Fallback: match by parameter structure (sub addresses and count)
    reaper.ShowConsoleMsg("Fallback: detecting effect by parameter structure\n")
    
    -- Count parameters by sub address (exclude system events)
    local sub_counts = {}
    for _, param in ipairs(params) do
        local sub = param.sub_address
        -- Exclude: sub=0x00 (EFX Type), sub=0x19 (EFX On/Off), sub=0x40~0x4F (Part settings)
        if sub ~= 0x00 and sub ~= 0x19 and (sub < 0x40 or sub > 0x4F) then
            sub_counts[sub] = (sub_counts[sub] or 0) + 1
        end
    end
    
    reaper.ShowConsoleMsg("Parameter distribution (effect params only): ")
    for sub, count in pairs(sub_counts) do
        reaper.ShowConsoleMsg(string.format("sub=%02X:%d ", sub, count))
    end
    reaper.ShowConsoleMsg("\n")
    
    -- Match effect by parameter sub distribution
    for i, effect in ipairs(app.effects) do
        local effect_sub_counts = {}
        for _, param in ipairs(effect.parameters) do
            local addr = param.address or {}
            local sub_str = addr["sub"] or addr.sub
            if sub_str then
                local sub = tonumber(sub_str, 16) or 0
                effect_sub_counts[sub] = (effect_sub_counts[sub] or 0) + 1
            end
        end
        
        -- Check if sub distributions match
        local match = true
        for sub, count in pairs(sub_counts) do
            if effect_sub_counts[sub] ~= count then
                match = false
                break
            end
        end
        
        if match then
            -- Also verify no extra subs in effect
            for sub, count in pairs(effect_sub_counts) do
                if not sub_counts[sub] then
                    match = false
                    break
                end
            end
        end
        
        if match then
            set_current_effect_index(i)
            reaper.ShowConsoleMsg(string.format("✓ Detected effect by param structure: %s\n", 
                effect.name))
            return
        end
    end
    
    reaper.ShowConsoleMsg(string.format("✗ Could not detect effect (found %d parameters)\n", #params))
end

-- ============================================================================
-- EFX A/B Helper Functions
-- ============================================================================

function get_current_efx_sub()
    -- 現在選択中のEFXのSubアドレスを返す (0x03 for A, 0x04 for B)
    return (app.current_efx == "B") and 0x04 or 0x03
end

function get_current_effect_index()
    -- エフェクトインデックスはA/Bで共有（subアドレスだけが違う）
    return app.selected_effect_index
end

function set_current_effect_index(index)
    -- エフェクトインデックスはA/Bで共有
    app.selected_effect_index = index
end

function get_current_parameters()
    -- 現在選択中のEFXのパラメータ配列を返す
    local params = (app.current_efx == "B") and app.efx_b_parameters or app.efx_a_parameters
    reaper.ShowConsoleMsg(string.format("[DEBUG] get_current_parameters: EFX %s, count=%d\n",
        app.current_efx, #params))
    if #params > 0 and #params <= 5 then
        for i, p in ipairs(params) do
            reaper.ShowConsoleMsg(string.format("  [%d] param_id=%s, value=%d\n", 
                i, p.param_id, p.value1))
        end
    end
    return params
end

function get_current_rdc_levels()
    -- 現在選択中のEFXのRDCレベルを返す
    local levels
    if app.current_efx == "B" then
        levels = {
            reverb = app.reverb_level_b,
            delay = app.delay_level_b,
            chorus = app.chorus_level_b
        }
    else
        levels = {
            reverb = app.reverb_level_a,
            delay = app.delay_level_a,
            chorus = app.chorus_level_a
        }
    end
    reaper.ShowConsoleMsg(string.format("[DEBUG] get_current_rdc_levels: EFX %s, Rev=%d, Del=%d, Chr=%d\n",
        app.current_efx, levels.reverb, levels.delay, levels.chorus))
    return levels
end

function set_current_parameters(params)
    -- 現在選択中のEFXにパラメータ配列を設定
    if app.current_efx == "B" then
        app.efx_b_parameters = params
    else
        app.efx_a_parameters = params
        app.current_parameters = params  -- 後方互換性のため
    end
end

-- ============================================================================
-- Original Helper Functions (modified to support A/B)
-- ============================================================================

function get_current_effect()
    local index = get_current_effect_index()
    if not index then
        return nil
    end
    return app.effects[index]
end

function get_parameter_values()
    local values = {}
    local params = get_current_parameters()
    local current_sub = get_current_efx_sub()
    
    reaper.ShowConsoleMsg(string.format("[DEBUG] get_parameter_values: current_efx=%s, sub=0x%02X, params count=%d\n",
        app.current_efx, current_sub, #params))
    
    -- MIDIパラメータのparam_idはsub_address依存 (e.g., "03_01" or "04_01")
    -- エフェクト定義のparam_idは常にsub=03ベース (e.g., "03_01")
    -- したがって、sub部分を変換してマッチングする
    for i, param in ipairs(params) do
        -- param.param_idは "03_01" または "04_01" のような形式
        -- これを常に "03_XX" 形式に正規化してエフェクト定義とマッチング
        local offset_part = string.match(param.param_id, "^%x%x_(%x%x)$")
        if offset_part then
            local normalized_id = string.format("03_%s", offset_part)
            values[normalized_id] = param.value1
            if i <= 5 then  -- 最初の5個だけログ出力
                reaper.ShowConsoleMsg(string.format("  [%d] param_id=%s -> normalized=%s, value=%d\n",
                    i, param.param_id, normalized_id, param.value1))
            end
        end
    end
    
    local count = 0
    for _ in pairs(values) do count = count + 1 end
    reaper.ShowConsoleMsg(string.format("[DEBUG] get_parameter_values: returning %d values\n", count))
    
    return values
end

function get_current_play_position_tick()
    if not app.midi_take then return nil end
    
    local play_state = reaper.GetPlayState()
    if play_state & 1 == 0 then  -- 再生中でない
        return nil
    end
    
    local item = reaper.GetMediaItemTake_Item(app.midi_take)
    if not item then return nil end
    
    local play_pos = reaper.GetPlayPosition()
    local item_start = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
    local relative_pos = play_pos - item_start
    
    if relative_pos < 0 then return nil end  -- アイテムより前
    
    -- 秒をtickに変換
    local ppq = reaper.MIDI_GetPPQPosFromProjTime(app.midi_take, play_pos)
    
    if not ppq then return nil end
    
    return math.floor(ppq + 0.5)
end

function update_parameter(param_id, new_value)
    local effect = get_current_effect()
    if not effect then return end
    
    -- param_idは常にsub=03ベース（エフェクト定義から来る）
    local param_info = nil
    for _, p in ipairs(effect.parameters) do
        local addr = p.address or {}
        local sub = tonumber(addr["sub"], 16) or 0
        local offset = tonumber(addr["offset"], 16) or 0
        local pid = string.format("%02X_%02X", sub, offset)
        
        if pid == param_id then
            param_info = p
            break
        end
    end
    
    if not param_info then return end
    
    -- Amp TypeでSmall無効の場合は1未満の値を1に制限
    if is_amp_type_small_disabled(param_info) and new_value < 1 then
        new_value = 1
    end
    
    local addr = param_info.address
    local sub = get_current_efx_sub()  -- 現在のEFXに応じたSubを使用（03 or 04）
    local offset = tonumber(addr["offset"], 16) or 0
    
    -- MIDIイベントに存在するか事前チェック
    local params = get_current_parameters()
    local actual_param_id = string.format("%02X_%02X", sub, offset)
    local found_in_midi = false
    for _, param in ipairs(params) do
        if param.param_id == actual_param_id then
            found_in_midi = true
            param.value1 = new_value  -- 値を更新
            break
        end
    end
    
    -- MIDIに存在しないパラメーターへの書き込みは静かにスキップ
    if not found_in_midi then
        return
    end
    
    -- MIDI書き込み（既存イベントを更新）
    local success = midi_utils.write_sysex_parameter(
        app.midi_take,
        app.current_part,
        0x40,
        sub,
        offset,
        new_value,
        0
    )
    
    if not success then
        reaper.ShowConsoleMsg(string.format("Failed to write param %s value %d\n", param_id, new_value))
        return
    end
    
end

function cleanup_temporary_events()
    if #app.temporary_events == 0 then return end
    
    local play_pos = reaper.GetPlayPosition()
    local play_state = reaper.GetPlayState()
    local is_playing = (play_state & 1) ~= 0
    
    -- 再生停止時、または再生ヘッドが一時アイテムを通過したら削除
    for i = #app.temporary_events, 1, -1 do
        local temp_event = app.temporary_events[i]
        local should_remove = false
        
        if not is_playing then
            -- 再生停止時は全ての一時アイテムを削除
            should_remove = true
            reaper.ShowConsoleMsg("[TEMP] Playback stopped, removing temporary item\n")
        elseif not reaper.ValidatePtr(temp_event.item, "MediaItem*") then
            -- 既に無効なアイテムなら管理配列から除去
            should_remove = true
        else
            local item_pos = reaper.GetMediaItemInfo_Value(temp_event.item, "D_POSITION")
            local item_len = reaper.GetMediaItemInfo_Value(temp_event.item, "D_LENGTH")
            local item_end = item_pos + item_len
            if play_pos >= item_end then
                should_remove = true
                reaper.ShowConsoleMsg(string.format("[TEMP] Playback passed item end %.3fs, removing temporary item\n", item_end))
            end
        end
        
        if should_remove then
            -- アイテムを削除
            if reaper.ValidatePtr(temp_event.item, "MediaItem*") then
                midi_utils.delete_temp_item(app.midi_track, temp_event.item)
            end
            
            -- 配列から削除
            table.remove(app.temporary_events, i)
        end
    end
end

function update_rdc_level(rdc_type, new_value)
    -- rdc_type: "reverb", "delay", "chorus"
    local offset_map = {
        reverb = 0x17,
        chorus = 0x18,
        delay = 0x19
    }
    
    local offset = offset_map[rdc_type]
    if not offset then return end
    
    -- 現在のEFX A/Bに応じたsubアドレスを取得
    local sub = get_current_efx_sub()
    
    -- 既存イベントを更新
    local success = midi_utils.write_rdc_level(
        app.midi_take,
        app.current_part,
        sub,
        offset,
        new_value
    )
    
    if not success then
        reaper.ShowConsoleMsg(string.format("Failed to write %s level %d\n", rdc_type, new_value))
        return
    end
    
    -- appの値を更新（現在のEFX A/Bに応じて）
    if app.current_efx == "B" then
        if rdc_type == "reverb" then
            app.reverb_level_b = new_value
        elseif rdc_type == "delay" then
            app.delay_level_b = new_value
        elseif rdc_type == "chorus" then
            app.chorus_level_b = new_value
        end
    else  -- EFX A
        if rdc_type == "reverb" then
            app.reverb_level_a = new_value
        elseif rdc_type == "delay" then
            app.delay_level_a = new_value
        elseif rdc_type == "chorus" then
            app.chorus_level_a = new_value
        end
    end
    
end

-- ============================================================================
-- System Effects Functions
-- ============================================================================

function read_system_effect_parameters()
    -- Read all system effect parameters from MIDI
    if not app.midi_take or not app.system_effects then
        return
    end
    
    -- Read all parameters from section 0x40 (same as EFX)
    local params = midi_utils.read_sysex_parameters(app.midi_take, app.current_part, 0x40)
    
    app.system_effect_values = {}
    -- MIDI にあるもの（サブ × 256 + オフセット）。画面で「!」（MIDI に無い）を付けるかに使う
    app.syseff_in_midi = {}
    for _, p in ipairs(params) do app.syseff_in_midi[p.sub_address * 256 + p.offset] = true end

    -- Map parameters to system effect values
    for _, param in ipairs(params) do
        local sub = param.sub_address
        local offset = param.offset
        local value = param.value1
        
        -- Determine which section this parameter belongs to
        local section_name = nil
        if sub == 0x01 then
            if offset >= 0x30 and offset <= 0x37 then
                section_name = "reverb"
            elseif offset >= 0x38 and offset <= 0x40 then
                section_name = "chorus"
            elseif offset >= 0x50 and offset <= 0x5A then
                section_name = "delay"
            end
        end
        
        if section_name then
            local param_id = string.format("%s_%02X_%02X", section_name, sub, offset)
            app.system_effect_values[param_id] = value
        end
    end
    
    reaper.ShowConsoleMsg(string.format("Read %d system effect parameters\n", 
        (function() local c=0 for _ in pairs(app.system_effect_values) do c=c+1 end return c end)()))
end

function write_system_effect_parameter(section_name, param, new_value)
    -- Write a system effect parameter to MIDI
    if not app.midi_take then return end
    
    local addr = param.address
    local sub = tonumber(addr.sub, 16) or 0
    local offset = tonumber(addr.offset, 16) or 0
    local param_id = string.format("%s_%02X_%02X", section_name, sub, offset)
    
    -- Update value
    app.system_effect_values[param_id] = new_value
    
    -- Write to MIDI (既存イベントを更新)
    local success = midi_utils.write_sysex_parameter(
        app.midi_take,
        app.current_part,
        0x40,
        sub,
        offset,
        new_value,
        0
    )
    
    if not success then
        reaper.ShowConsoleMsg(string.format("Failed to write system effect param %s\\n", param_id))
        return
    end
    
end

function convert_syseff_value_to_display(param, midi_value)
    -- System Effect専用の値変換
    local type_info = param.type_info
    
    -- 小さい範囲（0-7、0-15など）の特別処理
    if type_info and type_info.type == "numeric_range" then
        local param_max = type_info.max or 127
        if param_max <= 15 then
            -- MIDI値をそのまま表示（0-7ならMIDI値0→0、MIDI値7→7と表示）
            local display_val = math.min(math.floor(param_max), midi_value)
            return tostring(display_val)
        end
    end
    
    -- その他のパラメーターはEFXと同じ処理
    return value_converter.midi_to_display(param, midi_value)
end

-- ============================================================================
-- Effect Switching
-- ============================================================================

-- パラメーターの既定の値（MIDI の値）。効果を切り替えたときと、Ctrl+クリックで使う
function param_default_value(param)
    local default_value = 64  -- 一般的なデフォルト
    local type_info = param and param.type_info
    if param and param.default ~= nil then
        default_value = param.default
    elseif type_info and type_info.default ~= nil then
        default_value = type_info.default
    elseif type_info then
        if type_info.type == "discrete" then
            default_value = 0  -- 離散値は最初の選択肢
        elseif type_info.type == "numeric_range" and param.name and string.find(param.name, "Rate")
               and type_info.min < 1 and type_info.max > 1 then
            default_value = 100  -- Rate は 5.0Hz 付近
        end
    end
    if param and is_amp_type_small_disabled(param) and default_value < 1 then default_value = 1 end
    return default_value
end

function switch_effect(effect_index)
    local effect = app.effects[effect_index]
    if not effect then return end
    
    set_current_effect_index(effect_index)
    
    -- ========================================================================
    -- PRESERVE Reverb/Chorus/Delay send levels BEFORE rebuilding
    -- ========================================================================
    local rdc_levels = get_current_rdc_levels()
    local saved_reverb = rdc_levels.reverb or 0
    local saved_delay = rdc_levels.delay or 0
    local saved_chorus = rdc_levels.chorus or 0
    
    reaper.ShowConsoleMsg(string.format("[PRESERVE] Saving RDC levels: Reverb=%d, Delay=%d, Chorus=%d\n",
        saved_reverb, saved_delay, saved_chorus))
    
    local sub = get_current_efx_sub()  -- 現在のEFXに応じたSubを使用
    
    local new_params = {}
    for _, param in ipairs(effect.parameters) do
        local addr = param.address or {}
        local param_sub = tonumber(addr["sub"], 16) or 0
        local offset = tonumber(addr["offset"], 16) or 0
        local param_id = string.format("%02X_%02X", param_sub, offset)
        
        -- パラメーターのタイプに応じた適切なデフォルト値
        local default_value = param_default_value(param)

        table.insert(new_params, {
            param_id = param_id,
            sub_address = sub,  -- 現在のEFXに応じたSubを使用
            offset = offset,
            value1 = default_value,
            value2 = 0
        })
    end
    
    -- ========================================================================
    -- ADD Reverb/Chorus/Delay send levels to new_params
    -- ========================================================================
    table.insert(new_params, {
        param_id = string.format("%02X_17", sub),
        sub_address = sub,
        offset = 0x17,
        value1 = saved_reverb,
        value2 = 0
    })
    table.insert(new_params, {
        param_id = string.format("%02X_18", sub),
        sub_address = sub,
        offset = 0x18,
        value1 = saved_chorus,
        value2 = 0
    })
    table.insert(new_params, {
        param_id = string.format("%02X_19", sub),
        sub_address = sub,
        offset = 0x19,
        value1 = saved_delay,
        value2 = 0
    })
    
    reaper.ShowConsoleMsg(string.format("[PRESERVE] Added RDC to new_params: %d parameters total\n", #new_params))
    
    -- Extract SysEx values from "code" field (format: "01H, 10H" -> v1=0x01, v2=0x10)
    local effect_v1 = 0
    local effect_v2 = 0
    if effect.code then
        -- Parse both hex values from format "XXH, YYH"
        local first_hex, second_hex = effect.code:match("(%x+)H%s*,%s*(%x+)H")
        if first_hex and second_hex then
            effect_v1 = tonumber(first_hex, 16) or 0
            effect_v2 = tonumber(second_hex, 16) or 0
        end
    end
    
    -- Fallback to "no" field if code parsing fails
    if effect_v1 == 0 and effect_v2 == 0 and effect.no then
        local no = tonumber(effect.no) or 0
        effect_v1 = no > 0 and 0x01 or 0x00
        effect_v2 = no > 0 and (no - 1) or 0x00
    end
    
    local target_sub = get_current_efx_sub()  -- 現在のEFXのSubアドレス (0x03 or 0x04)
    midi_utils.rebuild_effect_sysex(app.midi_take, app.current_part, new_params, effect_v1, effect_v2, 0, target_sub)
    
    -- 現在のEFXにパラメータを設定
    set_current_parameters(new_params)
    
    -- ========================================================================
    -- VERIFY that RDC levels were properly restored
    -- ========================================================================
    local current_sub = get_current_efx_sub()
    reaper.ShowConsoleMsg("[PRESERVE] Verifying RDC restoration...\n")
    local verify_reverb = midi_utils.read_rdc_level(app.midi_take, app.current_part, 0x17, current_sub) or 0
    local verify_delay = midi_utils.read_rdc_level(app.midi_take, app.current_part, 0x18, current_sub) or 0
    local verify_chorus = midi_utils.read_rdc_level(app.midi_take, app.current_part, 0x19, current_sub) or 0
    
    reaper.ShowConsoleMsg(string.format("[PRESERVE] Verified: Reverb=%d (expected %d), Delay=%d (expected %d), Chorus=%d (expected %d)\n",
        verify_reverb, saved_reverb, verify_delay, saved_delay, verify_chorus, saved_chorus))
    
    -- 試聴：作り直した EFX の種類とパラメータを、種類 → パラメータの順に 20ms ずつあけて音源へ送る（一時アイテムは作らない）
    do
        local sub = get_current_efx_sub()
        local evs = {}
        local _, _, _, n_tx = reaper.MIDI_CountEvts(app.midi_take)
        for i = 0, n_tx - 1 do
            local ok, _, _, tick, typ, msg = reaper.MIDI_GetTextSysexEvt(app.midi_take, i)
            if ok and typ == -1 then
                local p = midi_utils.parse_sysex(msg)
                if p and p.part == app.current_part and p.section == 0x40 and p.sub == sub then
                    evs[#evs + 1] = {tick = tick, msg = msg, is_type = p.offset == 0x00}
                end
            end
        end
        table.sort(evs, function(a, b)
            if a.is_type ~= b.is_type then return a.is_type end
            return a.tick < b.tick
        end)
        for k, e in ipairs(evs) do live.send_sysex(e.msg, k == 1 and 0 or 20) end
    end

    -- ========================================================================
    -- CRITICAL FIX: エフェクト切り替え後、MIDIから実際のパラメータを再読み込み
    -- rebuild_effect_sysexでイベント位置が変わっているため、メモリ上のリストを更新
    -- ========================================================================
    reaper.ShowConsoleMsg("[FIX] Reloading parameters from MIDI after rebuild...\n")
    
    local current_sub = get_current_efx_sub()
    local reloaded_params = {}
    for _, p in ipairs(midi_utils.read_sysex_parameters(app.midi_take, app.current_part, 0x40)) do
        if p.sub_address == current_sub then
            table.insert(reloaded_params, p)
        end
    end
    
    -- 現在のEFXに再読み込みしたパラメータを設定
    set_current_parameters(reloaded_params)
    
    if app.current_efx == "A" then
        app.current_parameters = reloaded_params  -- 後方互換性のため
    end
    
    reaper.ShowConsoleMsg(string.format("[FIX] Reloaded %d parameters from MIDI (expected %d)\n", 
        #reloaded_params, #new_params))
    
    reaper.ShowConsoleMsg(string.format("Switched to effect: %s (v1=0x%02X v2=0x%02X, %d parameters)\n", 
        effect.name, effect_v1, effect_v2, #reloaded_params))
end

function main_loop()
    if not ui.is_open() then
        return
    end
    
    ui.begin_frame()

    -- このスクリプトに割り当てたショートカットが、この画面の中で押されたら閉じる
    local key = ui.state.last_char or 0
    if key > 0 and shortcut_pressed(key) then
        ui.close()
        return
    end
    -- ミキサー・ドラムのショートカット：そのスクリプトを実行（開く・前に出す・閉じる）
    if key > 0 and other_shortcut(key) then key = 0 end
    -- Space：XLaben_Play と同じ再生・停止。Ctrl+Z / Ctrl+Y：元に戻す / やり直し（ミキサーと同じ）
    if key == 32 and ok_play then xl_play.toggle(MIXER_DIR)
    elseif key == 26 then reaper.Main_OnCommand(40029, 0); app.last_check = -100
    elseif key == 25 then reaper.Main_OnCommand(40030, 0); app.last_check = -100 end
    if gfx.mouse_cap & 2 == 0 and (ui.state.right_was or false) then context_menu() end
    ui.state.right_was = gfx.mouse_cap & 2 == 2
    refresh_if_changed()
    if not app.midi_take then
        gfx.setfont(1, "Meiryo UI", 16)
        ui.text(20, 20, "X-Laben のセットアップの SysEx（A01 SYSTEM_EXCLUSIVE）が見つかりません。", ui.COLOR.TEXT)
        ui.draw_status()
        ui.end_frame()
        reaper.defer(main_loop)
        return
    end

    -- ホバー状態をリセット
    ui.state.hover_parameter = nil
    ui.state.hover_effect = nil
    
    -- view_modeに応じたcurrent_efxの設定
    if ui.state.view_mode == "efx_a" then
        app.current_efx = "A"
    elseif ui.state.view_mode == "efx_b" then
        app.current_efx = "B"
    end
    
    local effect = get_current_effect()
    local param_values = get_parameter_values()
    
    -- EFXボタンエリア
    local efx_buttons_x = ui.PADDING
    local efx_buttons_y = ui.PADDING
    local efx_buttons_w = ui.WINDOW_WIDTH - ui.PADDING * 2
    local efx_buttons_h = ui.EFX_BUTTONS_HEIGHT
    
    local current_effect_name = effect and effect.name or nil
    
    local clicked_track = ui.draw_efx_buttons(
        efx_buttons_x, efx_buttons_y,
        efx_buttons_w, efx_buttons_h,
        app.efx_on_states,
        current_effect_name
    )
    
    if clicked_track then
        -- 3状態サイクル: 0(OFF) → 1(A) → 2(B) → 0(OFF) ...
        local current_state = app.efx_on_states[clicked_track] or 0
        local new_state = (current_state + 1) % 3
        
        local success = midi_utils.write_efx_on_state(
            app.midi_take, 
            app.current_part, 
            clicked_track, 
            new_state  -- 0, 1, 2を直接渡す
        )
        
        if success then
            app.efx_on_states[clicked_track] = new_state
            
        end
    end
    
    -- A/B面切り替えボタン（左下）
    local view_mode_btn_x = ui.PADDING
    local view_mode_btn_y = efx_buttons_y + efx_buttons_h + ui.PADDING
    local view_mode_btn_w = 200
    local view_mode_btn_h = 30
    
    local clicked_mode = ui.draw_view_mode_button(
        view_mode_btn_x, view_mode_btn_y,
        view_mode_btn_w, view_mode_btn_h,
        ui.state.view_mode
    )
    
    if clicked_mode then
        ui.state.view_mode = clicked_mode
        
        -- EFX A/B切り替え時にアプリ状態を更新
        if clicked_mode == "efx_a" then
            app.current_efx = "A"
            detect_current_effect()  -- EFX Aのエフェクトを検出
            reaper.ShowConsoleMsg("Switched to EFX A view\n")
        elseif clicked_mode == "efx_b" then
            app.current_efx = "B"
            detect_current_effect()  -- EFX Bのエフェクトを検出
            reaper.ShowConsoleMsg("Switched to EFX B view\n")
        else
            reaper.ShowConsoleMsg(string.format("Switched to %s view\n", clicked_mode))
        end
        
        -- エフェクトを再取得
        effect = get_current_effect()
        param_values = get_parameter_values()
        ui.state.preset_set_mode = false
        ui.state.last_used_preset = nil
        ui.state.syseff_preset_set_mode = { reverb = false, chorus = false, delay = false }
    end
    
    -- ビューに応じた描画
    if ui.state.view_mode == "syseff" then
        -- System Effects表示
        local syseff_updates = ui.draw_syseff_view()
        
        -- システムエフェクトパラメーター更新
        for param_id, update_info in pairs(syseff_updates) do
            local section_name = string.match(param_id, "^([^_]+)")
            write_system_effect_parameter(section_name, update_info.param, update_info.value)
        end
    else
        -- EFX A/B表示（既存の処理）
        -- エフェクト名表示（EFX/SysEffボタンと同じ高さ）
        if effect then
            local effect_name_x = view_mode_btn_x + view_mode_btn_w + ui.PADDING
            local effect_name_y = view_mode_btn_y
            local effect_name_w = ui.WINDOW_WIDTH - effect_name_x - ui.PADDING
            local effect_name_h = view_mode_btn_h
            
            ui.draw_current_effect_name(
                effect_name_x,
                effect_name_y,
                effect_name_w,
                effect_name_h,
                effect.name,
                app.current_efx  -- "A" or "B"
            )
        end
        
        -- 左側: エフェクトリスト
        local effect_list_x = ui.PADDING
        local effect_list_y = view_mode_btn_y + view_mode_btn_h + ui.PADDING
        local effect_list_w = ui.EFFECT_LIST_WIDTH
        local effect_list_h = ui.WINDOW_HEIGHT - effect_list_y - ui.PADDING
        
        local new_effect_index = ui.draw_effect_list(
            effect_list_x,
            effect_list_y,
            effect_list_w,
            effect_list_h,
            app.effects,
            get_current_effect_index(),  -- 現在のEFXに応じたインデックス
            app.is_playing,  -- 再生状態を渡す
            ui.state.view_mode  -- ビューモードを渡す
        )
        
        -- 再生中でなければエフェクト切り替えを許可
        local current_index = get_current_effect_index()
        if new_effect_index and new_effect_index ~= current_index and not app.is_playing then
            switch_effect(new_effect_index)
            effect = get_current_effect()
            param_values = get_parameter_values()
            ui.state.preset_set_mode = false
            ui.state.last_used_preset = nil
        end
        
        -- 右側: パラメーターリストとRDCパネルとヘルプボックス
        if effect then
            local param_list_x = effect_list_x + effect_list_w + ui.PADDING
            local param_list_y = view_mode_btn_y + view_mode_btn_h + ui.PADDING
            local param_list_w = ui.WINDOW_WIDTH - param_list_x - ui.PADDING
            local param_list_h = ui.WINDOW_HEIGHT - param_list_y - ui.PADDING * 4 - ui.PRESET_PANEL_HEIGHT - ui.RDC_PANEL_HEIGHT - ui.HELP_BOX_HEIGHT
        
        local updated_values = ui.draw_parameter_list(
            param_list_x,
            param_list_y,
            param_list_w,
            param_list_h,
            effect.parameters,
            param_values
        )
        
        for param_id, new_value in pairs(updated_values) do
            update_parameter(param_id, new_value)
        end
        
        -- プリセットパネル (EFXパラメータとSend Levelsの間)
        local preset_panel_x = param_list_x
        local preset_panel_y = param_list_y + param_list_h + ui.PADDING
        local preset_panel_w = param_list_w
        local preset_panel_h = ui.PRESET_PANEL_HEIGHT

        local current_eff_no = effect.no or (get_current_effect_index() - 1)
        ui.draw_preset_panel(
            preset_panel_x,
            preset_panel_y,
            preset_panel_w,
            preset_panel_h,
            current_eff_no
        )

        -- RDCパネル
        local rdc_panel_x = param_list_x
        local rdc_panel_y = preset_panel_y + preset_panel_h + ui.PADDING
        local rdc_panel_w = param_list_w
        local rdc_panel_h = ui.RDC_PANEL_HEIGHT
        
        -- 現在のEFX A/BのRDCレベルを取得
        local rdc_levels = get_current_rdc_levels()
        
        local rdc_updated = ui.draw_rdc_panel(
            rdc_panel_x,
            rdc_panel_y,
            rdc_panel_w,
            rdc_panel_h,
            rdc_levels.reverb,
            rdc_levels.delay,
            rdc_levels.chorus
        )
        
        for rdc_type, new_value in pairs(rdc_updated) do
            update_rdc_level(rdc_type, new_value)
        end
        
        -- ヘルプボックス
        local help_box_x = param_list_x
        local help_box_y = rdc_panel_y + rdc_panel_h + ui.PADDING
        local help_box_w = param_list_w
        local help_box_h = ui.HELP_BOX_HEIGHT
        
        ui.draw_help_box(
            help_box_x,
            help_box_y,
            help_box_w,
            help_box_h,
            ui.state.hover_effect,
            ui.state.hover_parameter
        )
        end
    end  -- End of view_mode if-else

    ui.draw_status()
    ui.knob_flush()  -- 離した・ホイールを止めたら、試聴していた値を書く
    flush_undo()
    ui.end_frame()
    
    -- 再生状態の監視と一時イベントのクリーンアップ
    local play_state = reaper.GetPlayState()
    local is_playing = (play_state & 1) ~= 0
    
    -- 再生状態の変化を検出
    if is_playing ~= app.is_playing then
        app.is_playing = is_playing
        if is_playing then
            reaper.ShowConsoleMsg("[PLAYBACK] Started\n")
        else
            reaper.ShowConsoleMsg("[PLAYBACK] Stopped\n")
        end
    end
    
    -- 一時イベントのクリーンアップ
    cleanup_temporary_events()
    
    reaper.defer(main_loop)
end

function init()
    -- もう一度実行されたら：見えていれば閉じる、ドッキングの裏に隠れていれば前に出す（ミキサー・ドラムと同じ）
    if ok_win then
        if not win.start("XLabenEFX") then return end
    elseif reaper.set_action_options then
        reaper.set_action_options(1 | 4)
    end
    if not load_database() then
        return
    end
    
    if not connect_to_track() then
        return
    end
    
    reaper.atexit(function()
        if ok_win then win.finish("XLabenEFX")
        elseif reaper.set_action_options then reaper.set_action_options(8) end
        if app.pending_undo then reaper.Undo_OnStateChange("X-Laben EFX：" .. app.pending_undo) end
        ui.save_window()
    end)
    local live_ok, live_err = live.ensure()
    if not live_ok then
        reaper.ShowMessageBox("試聴（JSFX から音源へ直接送る）が使えません：\n" .. tostring(live_err) ..
            "\n\n値の書き込みはできますが、変えた音はその場では聞こえません。", "X-Laben EFX Controller", 0)
    end
    ui.init("X-Laben EFX Controller")
    
    reaper.defer(main_loop)
end

init()
