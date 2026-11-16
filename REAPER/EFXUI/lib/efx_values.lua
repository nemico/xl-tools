-- X-Laben EFX Controller：MIDI の値と表示の値の変換（xlaben_efx_controller.lua から分けた）

-- ============================================================================
-- Value Converter
-- ============================================================================
local value_converter = {}

function value_converter.midi_to_display(param, midi_value)
    local type_info = param.type_info
    
    -- type_infoがない場合は生の値を返す
    if not type_info then
        return tostring(midi_value)
    end
    
    local unit = type_info.unit or ""
    
    -- 変換テーブルを使用する場合（最優先）
    -- パラメーター自身に変換テーブルがある場合
    if param.conversion_table then
        local midi_str = tostring(midi_value)
        local value_str = param.conversion_table[midi_str]
        
        -- 変換テーブルにない値の場合、最大値を使用
        if not value_str then
            -- 最大のMIDIキーを探す
            local max_midi = 0
            for k, _ in pairs(param.conversion_table) do
                local midi_num = tonumber(k)
                if midi_num and midi_num > max_midi then
                    max_midi = midi_num
                end
            end
            if max_midi > 0 and midi_value > max_midi then
                value_str = param.conversion_table[tostring(max_midi)]
            end
        end
        
        if value_str then
            local numeric_value = tonumber(value_str)
            
            -- 1000ms以上は秒単位で表示
            if numeric_value and numeric_value >= 1000 and unit == "ms" then
                local sec_value = numeric_value / 1000.0
                if sec_value == math.floor(sec_value) then
                    return string.format("%ds", math.floor(sec_value))
                else
                    return string.format("%.1fs", sec_value)
                end
            end
            
            -- 単位を追加
            if unit ~= "" then
                local has_unit = string.find(value_str, unit, 1, true)
                if not has_unit and not string.find(value_str, "[a-zA-Z%%]") then
                    return value_str .. unit
                end
            end
            return value_str
        end
    end
    
    -- EFX用: appが存在し、databaseが利用可能な場合
    -- conversion_columnがある場合、変換テーブルから値を取得
    if param.conversion_column and _G.app and _G.app.database and _G.app.database.conversion_table then
        local midi_str = tostring(midi_value)
        local conversion = _G.app.database.conversion_table[midi_str]
        if conversion and conversion[param.conversion_column] then
            local value_str = conversion[param.conversion_column]
            
            -- 値が数値かどうかチェック
            local numeric_value = tonumber(value_str)
            
            -- 1000ms以上は秒単位で表示
            if numeric_value and numeric_value >= 1000 and unit == "ms" then
                local sec_value = numeric_value / 1000.0
                if sec_value == math.floor(sec_value) then
                    return string.format("%ds", math.floor(sec_value))
                else
                    return string.format("%.1fs", sec_value)
                end
            end
            
            -- 単位を追加（特殊文字列の場合も含む）
            if numeric_value and unit ~= "" then
                -- 数値の場合のみ単位を追加
                return value_str .. unit
            end
            -- 特殊文字列（"Bypass"、"L180(=R180)"など）はそのまま返す
            return value_str
        end
    end
    
    -- 離散値（スラッシュ区切り）- A/B/C → 0/1/2
    if type_info.type == "discrete" then
        if type_info.choices and type_info.choices[midi_value + 1] then
            return type_info.choices[midi_value + 1]
        end
        return tostring(midi_value)
    
    -- パン - L63～Center～R63 (現在のままでOK)
    elseif type_info.type == "pan" then
        if midi_value == 64 then
            return "Center"
        elseif midi_value < 64 then
            return string.format("L%d", 64 - midi_value)
        else
            return string.format("R%d", midi_value - 64)
        end
    
    -- テキスト型（特別な変換が必要）
    elseif type_info.type == "text" then
        local range_str = type_info.range_str or ""
        
        -- Feedback: -98%〜+98% の場合
        if string.find(range_str, "%%") then
            -- MIDI 0→-98%、MIDI 64→0%、MIDI 127→+98%
            -- 範囲を抽出
            local min_pct = -98
            local max_pct = 98
            local pct_value = min_pct + (midi_value / 127.0) * (max_pct - min_pct)
            if pct_value >= 0 then
                return string.format("+%d%%", math.floor(pct_value + 0.5))
            else
                return string.format("%d%%", math.floor(pct_value - 0.5))
            end
        
        -- Balance: D＞0E〜D0 ＜E または A＞0B〜A0 ＜B の場合
        elseif string.find(range_str, "＞") and string.find(range_str, "＜") then
            if midi_value == 64 then
                return "D50<E50"
            elseif midi_value < 64 then
                -- Dry側が強い: D100～D50
                local d_val = 100 - (64 - midi_value) * (50.0 / 64.0)
                local e_val = midi_value * (50.0 / 64.0)
                return string.format("D%d<E%d", math.floor(d_val + 0.5), math.floor(e_val + 0.5))
            else
                -- Effect側が強い: E50～E100
                local d_val = (127 - midi_value) * (50.0 / 63.0)
                local e_val = 50 + (midi_value - 64) * (50.0 / 63.0)
                return string.format("D%d<E%d", math.floor(d_val + 0.5), math.floor(e_val + 0.5))
            end
        end
        
        return tostring(midi_value)
    
    -- 数値範囲（64を中心とする）
    elseif type_info.type == "numeric_range" then
        local min_val = type_info.min or 0
        local max_val = type_info.max or 127
        local center = type_info.center or 0
        
        -- Rateパラメーターの特別処理: 0.05～10.0の複雑なマッピング
        -- 注意: conversion_columnがある場合は、変換テーブルが優先されるため、この処理は実行されない
        -- MIDI 0-99: 0.05刻み (0.05, 0.10, ..., 4.95)
        -- MIDI 100-127: 0.1刻み (5.0, 5.1, ..., 10.0)
        if not param.conversion_column and param.name and string.find(param.name, "Rate") and min_val < 1 and max_val > 1 then
            local rate_value
            if midi_value <= 99 then
                rate_value = midi_value * 0.05
            else
                rate_value = 5.0 + (midi_value - 100) * 0.1
            end
            if rate_value < min_val then rate_value = min_val end
            if rate_value > max_val then rate_value = max_val end
            local result = string.format("%.2f", rate_value)
            if unit ~= "" then
                return result .. unit
            end
            return result
        end
        
        -- 通常の数値範囲: MIDI値から表示値へマッピング
        local display_value
        
        -- MIDI範囲を抽出（data_rangeから）
        local midi_min, midi_max = 0, 127
        if type_info.data_range then
            local min_str, max_str = string.match(type_info.data_range, "(%x+)%s*%-%s*(%x+)")
            if min_str and max_str then
                midi_min = tonumber(min_str, 16) or 0
                midi_max = tonumber(max_str, 16) or 127
            end
        end
        
        -- min=0の範囲は常に0ベース範囲として扱う（centerの値は無視）
        if min_val == 0 then
            -- 0ベース範囲: MIDI値0→0、MIDI値127→max への線形マッピング
            -- data_rangeがある場合はそれを使用
            if type_info.data_range and midi_min ~= 0 then
                local midi_range = midi_max - midi_min
                local param_range = max_val - min_val
                if midi_range > 0 then
                    display_value = min_val + (midi_value - midi_min) * param_range / midi_range
                else
                    display_value = min_val
                end
            else
                -- data_rangeがない場合は0-127の範囲で線形マッピング
                local param_range = max_val - min_val
                if param_range > 0 then
                    display_value = min_val + (midi_value / 127.0) * param_range
                else
                    display_value = min_val
                end
            end
        elseif midi_min ~= 0 or (min_val ~= 0 and center == 0) then
            -- 線形マッピング範囲（4-500%など、MIDI 1-120 → 4-500）
            -- MIDI範囲からパラメーター範囲への線形変換
            local midi_range = midi_max - midi_min
            local param_range = max_val - min_val
            if midi_range > 0 then
                display_value = min_val + (midi_value - midi_min) * param_range / midi_range
            else
                display_value = min_val
            end
        else
            -- 中心ベース範囲: data_rangeがある場合はそれを使用、ない場合は64を中心に
            -- 例: data_range "34-4C" (52-76) で min=-12, max=+12の場合
            --     MIDI 52→-12、MIDI 64→0、MIDI 76→+12
            if type_info.data_range and midi_min ~= 0 then
                -- data_rangeを使用した正確なマッピング
                local midi_range = midi_max - midi_min
                local param_range = max_val - min_val
                if midi_range > 0 then
                    display_value = min_val + (midi_value - midi_min) * param_range / midi_range
                else
                    display_value = center
                end
            else
                -- data_rangeがない場合は64を中心にマッピング
                local offset = midi_value - 64
                display_value = center + offset
            end
        end
        
        -- 範囲チェック
        if display_value < min_val then display_value = min_val end
        if display_value > max_val then display_value = max_val end
        
        local result
        -- 小数点の扱い
        if min_val ~= math.floor(min_val) or max_val ~= math.floor(max_val) then
            result = string.format("%.2f", display_value)
        else
            result = string.format("%d", math.floor(display_value + 0.5))
        end
        
        if unit ~= "" then
            return result .. unit
        end
        return result
    
    else
        return tostring(midi_value)
    end
end

function value_converter.display_to_midi(param, display_str)
    local type_info = param.type_info
    
    if not type_info then
        return tonumber(display_str) or 0
    end
    
    -- 変換テーブルからの逆引き（最優先）
    if param.conversion_table then
        local unit = type_info.unit or ""
        -- 1. 完全一致
        for k, v in pairs(param.conversion_table) do
            local v_str = tostring(v)
            if v_str == display_str or (unit ~= "" and v_str .. unit == display_str) then
                return tonumber(k) or 0
            end
        end
        -- 2. 数値最寄り一致
        local target_num = tonumber(string.match(display_str, "([%d%.]+)"))
        if target_num then
            local best_k = nil
            local best_diff = 999999
            for k, v in pairs(param.conversion_table) do
                local v_num = tonumber(string.match(tostring(v), "([%d%.]+)"))
                if v_num then
                    local diff = math.abs(v_num - target_num)
                    if diff < best_diff then
                        best_diff = diff
                        best_k = tonumber(k)
                    end
                end
            end
            if best_k then return best_k end
        end
    end
    
    if type_info.type == "discrete" then
        if type_info.choices then
            for i, choice in ipairs(type_info.choices) do
                if choice == display_str then
                    return i - 1
                end
            end
        end
        return tonumber(display_str) or 0
    
    elseif type_info.type == "pan" then
        if display_str == "Center" then
            return 64
        else
            local dir, val = string.match(display_str, "([LR])(%d+)")
            if dir and val then
                val = tonumber(val)
                if dir == "L" then
                    return 64 - val
                else
                    return 64 + val
                end
            end
        end
        return 64
    
    elseif type_info.type == "text" then
        local range_str = type_info.range_str or ""
        
        -- Feedback: パーセント表記から変換
        if string.find(range_str, "%%") then
            local pct = tonumber(string.match(display_str, "([+-]?%d+)"))
            if pct then
                local midi_val = (pct + 98) * (127.0 / 196.0)
                return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
            end
        
        -- Balance: D＞0E形式から変換
        elseif string.find(range_str, "＞") and string.find(range_str, "＜") then
            local d_val, e_val = string.match(display_str, "D(%d+)<?E(%d+)")
            if d_val and e_val then
                d_val = tonumber(d_val)
                e_val = tonumber(e_val)
                
                if d_val > e_val then
                    -- Dry側が強い
                    local midi_val = 64 - (100 - d_val) * (64.0 / 50.0)
                    return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
                else
                    -- Effect側が強い
                    local midi_val = 64 + (e_val - 50) * (63.0 / 50.0)
                    return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
                end
            end
        end
        
        return tonumber(display_str) or 0
    
    elseif type_info.type == "numeric_range" then
        local num = tonumber(display_str)
        if not num then return 0 end
        
        local min_val = type_info.min or 0
        local max_val = type_info.max or 127
        local center = type_info.center or 0
        
        -- Rateパラメーターの特別処理: 逆変換
        -- 注意: conversion_columnがある場合は、変換テーブルを使用するため、この処理は不要
        if not param.conversion_column and param.name and string.find(param.name, "Rate") and min_val < 1 and max_val > 1 then
            local midi_val
            if num < 5.0 then
                midi_val = num / 0.05
            else
                midi_val = 100 + (num - 5.0) / 0.1
            end
            return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
        end
        
        -- MIDI範囲を抽出（data_rangeから）
        local midi_min, midi_max = 0, 127
        if type_info.data_range then
            local min_str, max_str = string.match(type_info.data_range, "(%x+)%s*%-%s*(%x+)")
            if min_str and max_str then
                midi_min = tonumber(min_str, 16) or 0
                midi_max = tonumber(max_str, 16) or 127
            end
        end
        
        -- min=0の範囲は常に0ベース範囲として扱う（centerの値は無視）
        if min_val == 0 then
            -- 0ベース範囲: 値0→MIDI値0、値max→MIDI値127（またはdata_rangeのmax）への線形マッピング
            local param_range = max_val - min_val
            if param_range > 0 then
                if type_info.data_range and midi_min ~= 0 then
                    local midi_range = midi_max - midi_min
                    local midi_val = midi_min + (num - min_val) * midi_range / param_range
                    return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
                else
                    local midi_val = (num / param_range) * 127.0
                    return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
                end
            else
                return 0
            end
        -- 通常の数値範囲: パラメーター値からMIDI値へ逆変換
        elseif type_info.data_range and midi_min ~= 0 then
            -- data_rangeを使用した正確な逆マッピング
            local midi_range = midi_max - midi_min
            local param_range = max_val - min_val
            if param_range > 0 then
                local midi_val = midi_min + (num - min_val) * midi_range / param_range
                return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
            else
                return midi_min
            end
        else
            -- data_rangeがない場合は64を中心にマッピング
            local offset = num - center
            local midi_val = 64 + offset
            return math.max(0, math.min(127, math.floor(midi_val + 0.5)))
        end
    
    else
        return tonumber(display_str) or 0
    end
end


return value_converter
