-- X-Laben EFX Controller：パートの番号の対応、試聴、SysEx を読む・書く（xlaben_efx_controller.lua から分けた）

-- ============================================================================
-- EFX ON/OFF Mapping
-- ============================================================================
-- MIDI解析結果に基づく正確なマッピング:
-- 0x40 = TR10 (リズムトラック)
-- 0x41-0x49 = TR1-TR9
-- 0x4A-0x4F = TR11-TR16
local TRACK_TO_SUB = {
    [1]  = 0x41, [2]  = 0x42, [3]  = 0x43, [4]  = 0x44,
    [5]  = 0x45, [6]  = 0x46, [7]  = 0x47, [8]  = 0x48,
    [9]  = 0x49, [10] = 0x40, [11] = 0x4A, [12] = 0x4B,
    [13] = 0x4C, [14] = 0x4D, [15] = 0x4E, [16] = 0x4F
}

local SUB_TO_TRACK = {}
for track, sub in pairs(TRACK_TO_SUB) do
    SUB_TO_TRACK[sub] = track
end

local BUTTON_ORDER = {10, 2, 3, 4, 5, 6, 7, 8, 9, 1, 11, 12, 13, 14, 15, 16}

-- ============================================================================
-- 試聴：X-Laben トラックの JSFX（A2M Live MIDI preview）から音源へ直接送る（一時 MIDI アイテムは作らない）。
-- XLabenMixer（となりのフォルダ）の部品をそのまま使う：送る（lib/xl_live.lua）、JSFX を入れる（lib/xl_project.lua）。
-- 見つからなければ試聴だけできない（値の書き込みはできる）。
-- ============================================================================
local HERE = debug.getinfo(1, "S").source:match("^@?(.*[\\/])") or ""
local MIXER_DIR = HERE .. "../../XLabenMixer/"
local ok_live, xl_live = pcall(dofile, MIXER_DIR .. "lib/xl_live.lua")
local ok_proj, xl_proj = pcall(dofile, MIXER_DIR .. "lib/xl_project.lua")

local live = {}

-- REAPER の形（F0 / F7 なし）の SysEx を送る。delay_ms：前のメッセージを送ってから待つ時間
function live.send_sysex(body, delay_ms)
    if not ok_live then return false end
    if body:byte(1) ~= 0xF0 then body = string.char(0xF0) .. body .. string.char(0xF7) end
    return xl_live.send(body, delay_ms)
end

-- X-Laben トラックの先頭に JSFX があるようにする（XLabenMixer と同じ決まり。古ければ入れ替える）
function live.ensure()
    if not (ok_live and ok_proj) then
        return false, "XLabenMixer の部品が見つかりません（" .. MIXER_DIR .. "）"
    end
    local tracks = xl_proj.find_tracks()
    if not tracks.xlaben then return false, "「X-Laben」トラックがありません" end
    return xl_proj.ensure_jsfx(tracks.xlaben, MIXER_DIR)
end

-- ============================================================================
-- MIDI/SysEx Utilities
-- ============================================================================
local midi_utils = {}

-- 書いたとき・書けなかったときに呼ぶ（本体が差し替える）。label は「元に戻す」に出す名前
midi_utils.on_edit = function(label) end

-- つまんでいる間（ドラッグ・ホイール）は、書かずに試聴だけする（ミキサーと同じ）。
-- begin_preview() のあとの write_sysex_parameter は、音源へ送って値を覚えるだけ。end_preview() で、覚えた最後の値を書く
midi_utils.deferred = nil
midi_utils.deferred_order = nil
function midi_utils.begin_preview()
    if not midi_utils.deferred then midi_utils.deferred, midi_utils.deferred_order = {}, {} end
end
function midi_utils.previewing() return midi_utils.deferred ~= nil end
function midi_utils.end_preview()
    local list, order = midi_utils.deferred, midi_utils.deferred_order
    midi_utils.deferred, midi_utils.deferred_order = nil, nil
    if not list then return 0 end
    for _, key in ipairs(order) do
        local a = list[key]
        midi_utils.write_sysex_parameter(a[1], a[2], a[3], a[4], a[5], a[6], a[7])
    end
    return #order
end
midi_utils.on_fail = function(message) end

function midi_utils.calculate_roland_checksum(bytes)
    -- Roland checksum: 128 - (sum of bytes % 128)
    -- X-Laben: Checksum range is from Address to Data (excluding part)
    -- Range: section, sub, offset, v1, [v2]
    local sum = 0
    for _, byte in ipairs(bytes) do
        sum = sum + byte
    end
    local checksum = 128 - (sum % 128)
    if checksum == 128 then
        checksum = 0
    end
    return checksum
end

function midi_utils.create_sysex(part, section, sub, offset, v1, v2, use_11byte_format, has_v2)
    -- Roland/X-Laben SysEx format with checksum
    -- has_v2: true if original message includes v2, false if only v1
    -- X-Laben checksum: calculated from section, sub, offset, v1, [v2] (NOT including part)
    
    local checksum_bytes
    if has_v2 then
        checksum_bytes = {section, sub, offset, v1, v2 or 0}
    else
        checksum_bytes = {section, sub, offset, v1}
    end
    local checksum = midi_utils.calculate_roland_checksum(checksum_bytes)
    
    -- REAPER automatically adds F0/F7
    if has_v2 then
        return string.pack("BBBBBBBBBBBB",
            0x00, 0x40, 0x04, 0x01, 0x58,
            part, section, sub, offset, v1, v2 or 0, checksum)
    else
        return string.pack("BBBBBBBBBBB",
            0x00, 0x40, 0x04, 0x01, 0x58,
            part, section, sub, offset, v1, checksum)
    end
end

function midi_utils.parse_sysex(msg)
    local len = #msg
    
    -- REAPER format with F0/F7
    -- 13 bytes: F0 00 40 04 01 58 [part] [section] [sub] [offset] [v1] [v2] [checksum] F7
    if len == 14 then
        local bytes = {string.byte(msg, 1, 14)}
        
        if bytes[1] == 0xF0 and bytes[14] == 0xF7 and
           bytes[2] == 0x00 and bytes[3] == 0x40 and bytes[4] == 0x04 and
           bytes[5] == 0x01 and bytes[6] == 0x58 then
            return {
                part = bytes[7],
                section = bytes[8],
                sub = bytes[9],
                offset = bytes[10],
                value1 = bytes[11],
                value2 = bytes[12],
                has_v2 = true
            }
        end
    end
    
    -- 12 bytes: F0 00 40 04 01 58 [part] [section] [sub] [offset] [v1] [checksum] F7 (no v2)
    if len == 13 then
        local bytes = {string.byte(msg, 1, 13)}
        
        if bytes[1] == 0xF0 and bytes[13] == 0xF7 and
           bytes[2] == 0x00 and bytes[3] == 0x40 and bytes[4] == 0x04 and
           bytes[5] == 0x01 and bytes[6] == 0x58 then
            return {
                part = bytes[7],
                section = bytes[8],
                sub = bytes[9],
                offset = bytes[10],
                value1 = bytes[11],
                value2 = 0,
                has_v2 = false
            }
        end
    end
    
    -- Without F0/F7
    -- 11 bytes without v2: 00 40 04 01 58 [part] [section] [sub] [offset] [v1] [checksum]
    if len == 11 then
        local bytes = {string.byte(msg, 1, 11)}
        
        if bytes[1] == 0x00 and bytes[2] == 0x40 and bytes[3] == 0x04 and
           bytes[4] == 0x01 and bytes[5] == 0x58 then
            return {
                part = bytes[6],
                section = bytes[7],
                sub = bytes[8],
                offset = bytes[9],
                value1 = bytes[10],
                value2 = 0,
                has_v2 = false
            }
        end
    end
    
    -- 12 bytes with v2: 00 40 04 01 58 [part] [section] [sub] [offset] [v1] [v2] [checksum]
    if len == 12 then
        local bytes = {string.byte(msg, 1, 12)}
        
        if bytes[1] == 0x00 and bytes[2] == 0x40 and bytes[3] == 0x04 and
           bytes[4] == 0x01 and bytes[5] == 0x58 then
            return {
                part = bytes[6],
                section = bytes[7],
                sub = bytes[8],
                offset = bytes[9],
                value1 = bytes[10],
                value2 = bytes[11],
                has_v2 = true
            }
        end
    end
    
    return nil
end

function midi_utils.find_sysex_track()
    local track_count = reaper.CountTracks(0)
    
    reaper.ShowConsoleMsg(string.format("Searching %d tracks...\n", track_count))
    
    for track_idx = 0, track_count - 1 do
        local track = reaper.GetTrack(0, track_idx)
        local _, track_name = reaper.GetTrackName(track)
        
        reaper.ShowConsoleMsg(string.format("Track %d: '%s'\n", track_idx + 1, track_name))
        
        -- Check if track name contains "SYSTEM_EXCLUSIVE"
        if string.find(track_name, "SYSTEM_EXCLUSIVE", 1, true) then
            reaper.ShowConsoleMsg(string.format("Found SYSTEM_EXCLUSIVE track: '%s'\n", track_name))
            
            local item_count = reaper.CountTrackMediaItems(track)
            reaper.ShowConsoleMsg(string.format("  Items on track: %d\n", item_count))
            
            -- Search through all items on this track
            for item_idx = 0, item_count - 1 do
                local item = reaper.GetTrackMediaItem(track, item_idx)
                local take = reaper.GetActiveTake(item)
                
                if take then
                    local is_midi = reaper.TakeIsMIDI(take)
                    reaper.ShowConsoleMsg(string.format("  Item %d: MIDI=%s\n", item_idx + 1, tostring(is_midi)))
                    
                    if is_midi then
                        local _, note_count, cc_count, sysex_count = reaper.MIDI_CountEvts(take)
                        reaper.ShowConsoleMsg(string.format("    Notes: %d, CCs: %d, SysEx: %d\n", note_count, cc_count, sysex_count))
                        
                        -- Check SysEx/Text events
                        for evt_idx = 0, sysex_count - 1 do
                            local _, _, _, _, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, evt_idx)
                            
                            if evt_type == -1 then  -- -1 = SysEx event
                                -- Debug: print first SysEx message details
                                if evt_idx == 0 then
                                    reaper.ShowConsoleMsg(string.format("    First SysEx length: %d bytes\n", #msg))
                                    local hex = ""
                                    for i = 1, math.min(14, #msg) do
                                        hex = hex .. string.format("%02X ", string.byte(msg, i))
                                    end
                                    reaper.ShowConsoleMsg(string.format("    First bytes: %s\n", hex))
                                end
                                
                                local parsed = midi_utils.parse_sysex(msg)
                                
                                if parsed then
                                    reaper.ShowConsoleMsg("    Found X-Laben SysEx! Using this take.\n")
                                    return take
                                end
                            end
                        end
                    end
                end
            end
        end
    end
    
    return nil
end

function midi_utils.read_sysex_parameters(take, part, section)
    local params = {}
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    
    reaper.ShowConsoleMsg(string.format("Reading SysEx: total %d events, looking for part=0x%02X section=0x%02X\n", 
        sysex_count, part, section))
    
    local parsed_count = 0
    local matched_count = 0
    
    for i = 0, sysex_count - 1 do
        local _, selected, muted, tick, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        
        if evt_type == -1 then  -- SysEx event
            local parsed = midi_utils.parse_sysex(msg)
            
            if parsed then
                parsed_count = parsed_count + 1
                
                -- Debug: show sub=03, offset=00 specifically
                if parsed.part == part and parsed.section == section and 
                   parsed.sub == 0x03 and parsed.offset == 0x00 then
                    reaper.ShowConsoleMsg(string.format(">>> Found sub=03 offset=00 at index %d: value1=0x%02X value2=0x%02X\n",
                        i, parsed.value1, parsed.value2))
                end
                
                if parsed.part == part and parsed.section == section then
                    matched_count = matched_count + 1
                    table.insert(params, {
                        event_index = i,
                        tick = tick,
                        part = parsed.part,
                        section = parsed.section,
                        sub_address = parsed.sub,
                        offset = parsed.offset,
                        value1 = parsed.value1,
                        value2 = parsed.value2,
                        param_id = string.format("%02X_%02X", parsed.sub, parsed.offset)
                    })
                end
            end
        end
    end
    
    reaper.ShowConsoleMsg(string.format("Parsed %d events, matched %d events for part=0x%02X section=0x%02X\n",
        parsed_count, matched_count, part, section))
    
    return params
end

function midi_utils.write_sysex_parameter(take, part, section, sub, offset, v1, v2)
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    local found = false
    
    reaper.ShowConsoleMsg(string.format("[DEBUG] write_sysex_parameter: Looking for part=0x%02X section=0x%02X sub=0x%02X offset=0x%02X (total %d events)\n",
        part, section, sub, offset, sysex_count))
    
    for i = 0, sysex_count - 1 do
        local _, selected, muted, tick, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            
            if parsed and parsed.part == part and parsed.section == section and
               parsed.sub == sub and parsed.offset == offset then
                -- Detect original format: 11-byte or 12-byte, and whether it has v2
                local use_11byte = (#msg == 11 or #msg == 13)  -- 11 or 13 (with F0/F7) = no 0x0C prefix
                local has_v2 = parsed.has_v2 or false
                reaper.ShowConsoleMsg(string.format("[DEBUG] Original format: %d bytes, use_11byte=%s, has_v2=%s\n", 
                    #msg, tostring(use_11byte), tostring(has_v2)))
                
                local new_msg = midi_utils.create_sysex(part, section, sub, offset, v1, v2, use_11byte, has_v2)
                
                -- デバッグ: 更新前のメッセージ
                local old_hex = ""
                for b = 1, math.min(14, #msg) do
                    old_hex = old_hex .. string.format("%02X ", string.byte(msg, b))
                end
                reaper.ShowConsoleMsg(string.format("[DEBUG] OLD msg[%d bytes]: %s\n", #msg, old_hex))
                
                -- デバッグ: 新しいメッセージ
                local new_hex = ""
                for b = 1, #new_msg do
                    new_hex = new_hex .. string.format("%02X ", string.byte(new_msg, b))
                end
                reaper.ShowConsoleMsg(string.format("[DEBUG] NEW msg[%d bytes]: %s\n", #new_msg, new_hex))
                
                if midi_utils.deferred then
                    -- つまんでいる間：送るだけ。離したら書く
                    live.send_sysex(new_msg, 1)
                    local key = string.format("%d:%d:%d:%d", part, section, sub, offset)
                    if not midi_utils.deferred[key] then midi_utils.deferred_order[#midi_utils.deferred_order + 1] = key end
                    midi_utils.deferred[key] = {take, part, section, sub, offset, v1, v2}
                    return true
                end
                reaper.MIDI_SetTextSysexEvt(take, i, selected, muted, tick, evt_type, new_msg)
                live.send_sysex(new_msg, 1)  -- 試聴：書いたものと同じメッセージを、その場で音源へ
                
                -- 更新後、すぐに読み直して検証
                local ret, sel2, mut2, tick2, type2, msg2 = reaper.MIDI_GetTextSysexEvt(take, i)
                if ret then
                    local verify_hex = ""
                    for b = 1, math.min(14, #msg2) do
                        verify_hex = verify_hex .. string.format("%02X ", string.byte(msg2, b))
                    end
                    reaper.ShowConsoleMsg(string.format("[DEBUG] VERIFY msg[%d bytes]: %s\n", #msg2, verify_hex))
                    
                    local parsed2 = midi_utils.parse_sysex(msg2)
                    if parsed2 then
                        reaper.ShowConsoleMsg(string.format("[DEBUG] ✓ Parse OK: part=0x%02X sec=0x%02X sub=0x%02X off=0x%02X v1=0x%02X v2=0x%02X\n",
                            parsed2.part, parsed2.section, parsed2.sub, parsed2.offset, parsed2.value1, parsed2.value2))
                    else
                        reaper.ShowConsoleMsg("[ERROR] ✗ Parse FAILED after update!\n")
                    end
                end
                
                reaper.ShowConsoleMsg(string.format("[DEBUG] Found and UPDATED event at index %d (tick=%d): v1=0x%02X v2=0x%02X\n",
                    i, tick, v1, v2))
                found = true
                break
            end
        end
    end
    
    if not found then
        reaper.ShowConsoleMsg(string.format("[ERROR] Event NOT FOUND! part=0x%02X section=0x%02X sub=0x%02X offset=0x%02X - NOT INSERTING\n",
            part, section, sub, offset))
        midi_utils.on_fail(string.format("書けませんでした：アドレス %02X %02X %02X の SysEx がセットアップにありません（足すことはしません）",
            section, sub, offset))
        return false
    end
    
    reaper.MIDI_Sort(take)
    midi_utils.on_edit("EFX の値を変更")
    return true
end

function midi_utils.read_efx_on_states(take, part)
    local efx_states = {}
    
    -- 初期化: 全トラックをOFFとする
    for i = 1, 16 do
        efx_states[i] = 0
    end
    
    -- SYSEXイベントをスキャン
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    
    reaper.ShowConsoleMsg(string.format("[EFX] Scanning %d sysex events for EFX ON/OFF states...\n", sysex_count))
    
    for i = 0, sysex_count - 1 do
        local _, _, _, _, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            
            if parsed and parsed.part == part and 
               parsed.section == 0x40 and parsed.offset == 0x22 then
                
                local track_num = SUB_TO_TRACK[parsed.sub]
                if track_num then
                    efx_states[track_num] = parsed.value1
                    reaper.ShowConsoleMsg(string.format("[EFX] TR%02d (sub=0x%02X): %s\n", 
                        track_num, parsed.sub, parsed.value1 > 0 and "ON" or "OFF"))
                end
            end
        end
    end
    
    return efx_states
end

function midi_utils.read_rdc_level(take, part, offset, sub)
    -- Reverb(0x17), Delay(0x18), Chorus(0x19)のレベルを読み取る
    -- sub: 0x03 for EFX A, 0x04 for EFX B
    sub = sub or 0x03  -- デフォルトはEFX A
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    
    for i = 0, sysex_count - 1 do
        local _, _, _, _, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            
            if parsed and parsed.part == part and 
               parsed.section == 0x40 and parsed.sub == sub and
               parsed.offset == offset then
                return parsed.value1
            end
        end
    end
    
    return 0  -- デフォルト値
end

function midi_utils.write_efx_on_state(take, part, track_num, state_value)
    local sub = TRACK_TO_SUB[track_num]
    if not sub then
        reaper.ShowConsoleMsg(string.format("[ERROR] Invalid track number: %d\n", track_num))
        return false
    end
    
    -- state_value: 0=OFF, 1=EFX A, 2=EFX B
    local v1 = state_value or 0
    
    -- 既存イベントのv2を読み取る（チェックサム計算用）
    local existing_v2 = 0
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    
    for i = 0, sysex_count - 1 do
        local _, _, _, _, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            if parsed and parsed.part == part and parsed.section == 0x40 and
               parsed.sub == sub and parsed.offset == 0x22 then
                existing_v2 = parsed.value2
                break
            end
        end
    end
    
    reaper.ShowConsoleMsg(string.format("[EFX] Setting TR%d (sub=0x%02X) to state=%d (v1=0x%02X, v2=0x%02X)\n", 
        track_num, sub, v1, v1, existing_v2))
    
    local success = midi_utils.write_sysex_parameter(
        take, part, 0x40, sub, 0x22, v1, existing_v2
    )
    
    if success then
        local state_name = (v1 == 2) and "B" or (v1 == 1) and "A" or "OFF"
        reaper.ShowConsoleMsg(string.format("[EFX] ✓ TR%d EFX %s\n", 
            track_num, state_name))
    else
        reaper.ShowConsoleMsg(string.format("[EFX] ✗ Failed to set TR%d EFX state\n", track_num))
    end
    
    return success
end

function midi_utils.write_rdc_level(take, part, sub, offset, level)
    -- Reverb(0x17), Delay(0x18), Chorus(0x19)のレベルを書き込む
    level = math.max(0, math.min(127, level))  -- 0-127にクランプ
    
    reaper.ShowConsoleMsg(string.format("[RDC] Setting sub=0x%02X offset=0x%02X to level %d\n", sub, offset, level))
    
    local success = midi_utils.write_sysex_parameter(
        take, part, 0x40, sub, offset, level, 0
    )
    
    return success
end

function midi_utils.insert_sysex_event_as_item(track, part, section, sub, offset, v1, v2, position_seconds)
    -- 新しいMIDIアイテムを作成して、そこにSysExイベントを挿入
    if not track then
        reaper.ShowConsoleMsg("[TEMP ERROR] Track is nil\n")
        return nil
    end
    
    local item_length = 0.01  -- 10msの短いアイテム
    
    -- 新しいMIDIアイテムを作成
    local item = reaper.CreateNewMIDIItemInProj(track, position_seconds, position_seconds + item_length)
    if not item then
        reaper.ShowConsoleMsg("[TEMP ERROR] Failed to create MIDI item\n")
        return nil
    end
    
    local take = reaper.GetActiveTake(item)
    if not take then
        reaper.DeleteTrackMediaItem(track, item)
        reaper.ShowConsoleMsg("[TEMP ERROR] Failed to get take from new item\n")
        return nil
    end
    
    -- 11バイト形式を検出（元のイベントから）
    local use_11byte = true  -- X-Labenは通常11バイト
    local has_v2 = false
    local new_msg = midi_utils.create_sysex(part, section, sub, offset, v1, v2, use_11byte, has_v2)
    
    -- SysExイベントをtick=0に挿入（アイテムの開始位置）
    local success = reaper.MIDI_InsertTextSysexEvt(take, false, false, 0, -1, new_msg)
    
    if success then
        reaper.ShowConsoleMsg(string.format("[TEMP] ✓ Created item at %.3fs: part=0x%02X sec=0x%02X sub=0x%02X off=0x%02X v1=0x%02X v2=0x%02X\n",
            position_seconds, part, section, sub, offset, v1, v2))
        reaper.UpdateItemInProject(item)
        reaper.MarkProjectDirty(0)
        return item
    else
        reaper.ShowConsoleMsg("[TEMP ERROR] Failed to insert sysex into new item\n")
        reaper.DeleteTrackMediaItem(track, item)
        return nil
    end
end

function midi_utils.delete_temp_item(track, item)
    -- 一時的なMIDIアイテムを削除
    if not track or not item then
        reaper.ShowConsoleMsg("[TEMP ERROR] Track or item is nil\n")
        return false
    end
    
    reaper.DeleteTrackMediaItem(track, item)
    reaper.ShowConsoleMsg("[TEMP] ✓ Deleted temporary item\n")
    reaper.UpdateArrange()
    
    return true
end

function midi_utils.rebuild_effect_sysex(take, part, parameters, effect_v1, effect_v2, base_tick, target_sub)
    -- ============================================================================
    -- エフェクト切り替え時のSysEx再配置（標準相対連動ルール）
    -- ============================================================================
    -- target_sub: 0x03=EFX A, 0x04=EFX B
    -- 
    -- 1. Section 0x00 および Section 0x40 Sub 0x00/0x01（基本/SysEff）は一切削除・変更せず位置保持
    -- 2. EFX A (Sub 0x03):
    --    - Type (0x00): 元のEFX A開始位置 (直前イベントから+10 tick以上確保)
    --    - Params (0x01〜0x16): 10 tick刻みで順次配置
    --    - Send (0x17 Reverb, 0x18 Chorus, 0x19 Delay): 最後のパラメータから +80 tick 後に10 tick刻みで配置
    -- 3. EFX B (Sub 0x04):
    --    - Type (0x00): EFX A Send Delay (0x19) から +10 tick 後（R02同一Step禁止規約）
    --    - Params (0x01〜0x16): 10 tick刻みで順次配置
    --    - Send (0x17 Reverb, 0x18 Chorus, 0x19 Delay): 最後のパラメータから +10 tick 後に10 tick刻みで配置
    -- 4. Part Assign (Sub 0x40〜0x4F):
    --    - EFX B Send Delay (0x19) から +10 tick 後から開始（R02同一Step禁止規約）、TR1〜16を10 tick刻みで順次配置
    -- ============================================================================
    
    local log_file = nil  -- Debug log (set to io.open(...) if needed)
    local function log(msg)
        if log_file then
            log_file:write(msg .. "\n")
            log_file:flush()
        end
    end
    
    local _, _, _, sysex_count = reaper.MIDI_CountEvts(take)
    log(string.rep("=", 80))
    log(string.format("REBUILD EFFECT SYSEX: Target=%s (sub=0x%02X)", 
        target_sub == 0x03 and "EFX A" or "EFX B", target_sub))
    log(string.format("Effect Type: v1=0x%02X v2=0x%02X, Parameters: %d", 
        effect_v1, effect_v2, #parameters))
    log(string.rep("=", 80))
    
    -- PHASE 1: 既存イベントの収集
    local efx_a_start_tick = nil
    local last_pre_efx_tick = 230
    
    local existing_efx_a = {
        type_v1 = 0, type_v2 = 0, has_type = false,
        params = {},
        send_rev = 0, send_cho = 0, send_del = 0, has_send = false
    }
    local existing_efx_b = {
        type_v1 = 0, type_v2 = 0, has_type = false,
        params = {},
        send_rev = 0, send_cho = 0, send_del = 0, has_send = false
    }
    local existing_part_assign = {}
    
    for i = 0, sysex_count - 1 do
        local _, selected, muted, tick, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            if parsed and parsed.part == part and parsed.section == 0x40 then
                if parsed.sub == 0x00 or parsed.sub == 0x01 then
                    if tick > last_pre_efx_tick then
                        last_pre_efx_tick = tick
                    end
                elseif parsed.sub == 0x03 then
                    if parsed.offset == 0x00 then
                        existing_efx_a.type_v1 = parsed.value1
                        existing_efx_a.type_v2 = parsed.value2 or 0
                        existing_efx_a.has_type = true
                        if not efx_a_start_tick then
                            efx_a_start_tick = tick
                        end
                    elseif parsed.offset == 0x17 then
                        existing_efx_a.send_rev = parsed.value1
                        existing_efx_a.has_send = true
                    elseif parsed.offset == 0x18 then
                        existing_efx_a.send_cho = parsed.value1
                        existing_efx_a.has_send = true
                    elseif parsed.offset == 0x19 then
                        existing_efx_a.send_del = parsed.value1
                        existing_efx_a.has_send = true
                    else
                        table.insert(existing_efx_a.params, {
                            offset = parsed.offset,
                            v1 = parsed.value1,
                            v2 = parsed.value2 or 0
                        })
                    end
                    if not efx_a_start_tick and tick then
                        efx_a_start_tick = tick
                    end
                elseif parsed.sub == 0x04 then
                    if parsed.offset == 0x00 then
                        existing_efx_b.type_v1 = parsed.value1
                        existing_efx_b.type_v2 = parsed.value2 or 0
                        existing_efx_b.has_type = true
                    elseif parsed.offset == 0x17 then
                        existing_efx_b.send_rev = parsed.value1
                        existing_efx_b.has_send = true
                    elseif parsed.offset == 0x18 then
                        existing_efx_b.send_cho = parsed.value1
                        existing_efx_b.has_send = true
                    elseif parsed.offset == 0x19 then
                        existing_efx_b.send_del = parsed.value1
                        existing_efx_b.has_send = true
                    else
                        table.insert(existing_efx_b.params, {
                            offset = parsed.offset,
                            v1 = parsed.value1,
                            v2 = parsed.value2 or 0
                        })
                    end
                elseif parsed.sub >= 0x40 and parsed.sub <= 0x4F then
                    table.insert(existing_part_assign, {
                        sub = parsed.sub,
                        offset = parsed.offset,
                        v1 = parsed.value1,
                        v2 = parsed.value2 or 0
                    })
                end
            end
        end
    end
    
    if not efx_a_start_tick or efx_a_start_tick <= last_pre_efx_tick then
        efx_a_start_tick = last_pre_efx_tick + 10
    end
    log(string.format("EFX A start tick determined: %d", efx_a_start_tick))
    
    -- PHASE 2: 変更対象セクション（Sub 0x03, 0x04, 0x40-0x4F）のみ削除
    -- Section 0x00, Section 0x40 Sub 0x00/0x01 などは一切削除しない
    local delete_count = 0
    for i = sysex_count - 1, 0, -1 do
        local _, selected, muted, tick, evt_type, msg = reaper.MIDI_GetTextSysexEvt(take, i)
        if evt_type == -1 then
            local parsed = midi_utils.parse_sysex(msg)
            if parsed and parsed.part == part and parsed.section == 0x40 then
                if parsed.sub == 0x03 or parsed.sub == 0x04 or (parsed.sub >= 0x40 and parsed.sub <= 0x4F) then
                    reaper.MIDI_DeleteTextSysexEvt(take, i)
                    delete_count = delete_count + 1
                end
            end
        end
    end
    log(string.format("Deleted %d existing EFX/Assign events (Sub 0x03, 0x04, 0x40-0x4F)", delete_count))
    
    -- PHASE 3: 新しい構成の準備
    local target_efx_a = {
        type_v1 = (target_sub == 0x03) and effect_v1 or existing_efx_a.type_v1,
        type_v2 = (target_sub == 0x03) and effect_v2 or existing_efx_a.type_v2,
        has_type = (target_sub == 0x03) or existing_efx_a.has_type,
        params = {},
        send_rev = existing_efx_a.send_rev,
        send_cho = existing_efx_a.send_cho,
        send_del = existing_efx_a.send_del
    }
    
    local target_efx_b = {
        type_v1 = (target_sub == 0x04) and effect_v1 or existing_efx_b.type_v1,
        type_v2 = (target_sub == 0x04) and effect_v2 or existing_efx_b.type_v2,
        has_type = (target_sub == 0x04) or existing_efx_b.has_type,
        params = {},
        send_rev = existing_efx_b.send_rev,
        send_cho = existing_efx_b.send_cho,
        send_del = existing_efx_b.send_del
    }
    
    -- パラメータの振り分け
    if target_sub == 0x03 then
        for _, p in ipairs(parameters) do
            if p.offset == 0x17 then
                target_efx_a.send_rev = p.value1
            elseif p.offset == 0x18 then
                target_efx_a.send_cho = p.value1
            elseif p.offset == 0x19 then
                target_efx_a.send_del = p.value1
            elseif p.offset >= 0x01 and p.offset <= 0x16 then
                table.insert(target_efx_a.params, {offset = p.offset, v1 = p.value1, v2 = p.value2 or 0})
            end
        end
        target_efx_b.params = existing_efx_b.params
    else
        target_efx_a.params = existing_efx_a.params
        for _, p in ipairs(parameters) do
            if p.offset == 0x17 then
                target_efx_b.send_rev = p.value1
            elseif p.offset == 0x18 then
                target_efx_b.send_cho = p.value1
            elseif p.offset == 0x19 then
                target_efx_b.send_del = p.value1
            elseif p.offset >= 0x01 and p.offset <= 0x16 then
                table.insert(target_efx_b.params, {offset = p.offset, v1 = p.value1, v2 = p.value2 or 0})
            end
        end
    end
    
    -- オフセット順にソート
    table.sort(target_efx_a.params, function(a, b) return a.offset < b.offset end)
    table.sort(target_efx_b.params, function(a, b) return a.offset < b.offset end)
    
    local function insert_event(tick, section, sub, offset, v1, v2, has_v2)
        local msg = midi_utils.create_sysex(part, section, sub, offset, v1, v2 or 0, true, has_v2)
        reaper.MIDI_InsertTextSysexEvt(take, false, false, tick, -1, msg)
        log(string.format("  Insert tick=%5d sub=0x%02X off=0x%02X v1=0x%02X v2=0x%02X",
            tick, sub, offset, v1, v2 or 0))
    end
    
    -- PHASE 4: 動的再配置
    -- 1. EFX A Type
    local current_tick = efx_a_start_tick
    log(string.format("Writing EFX A starting at tick %d...", current_tick))
    if target_efx_a.has_type then
        insert_event(current_tick, 0x40, 0x03, 0x00, target_efx_a.type_v1, target_efx_a.type_v2, true)
    end
    
    -- 2. EFX A Params (10 tick 刻み)
    for _, p in ipairs(target_efx_a.params) do
        current_tick = current_tick + 10
        insert_event(current_tick, 0x40, 0x03, p.offset, p.v1, p.v2, false)
    end
    local last_efx_a_param_tick = current_tick
    
    -- 3. EFX A Send Levels (固定スロット: efx_a_start_tick + 210)
    local efx_a_send_rev_tick = math.max(last_efx_a_param_tick + 10, efx_a_start_tick + 210)
    insert_event(efx_a_send_rev_tick,      0x40, 0x03, 0x17, target_efx_a.send_rev, 0, false)
    insert_event(efx_a_send_rev_tick + 10, 0x40, 0x03, 0x18, target_efx_a.send_cho, 0, false)
    insert_event(efx_a_send_rev_tick + 20, 0x40, 0x03, 0x19, target_efx_a.send_del, 0, false)
    local efx_a_end_tick = efx_a_send_rev_tick + 20
    
    local efx_b_end_tick = efx_a_end_tick
    
    -- 4. EFX B (存在する場合)
    if target_efx_b.has_type or #target_efx_b.params > 0 then
        -- R02規格規約: SysExが同一Stepに重複することは禁止のため、EFX A末尾から+10 tick空ける
        local efx_b_type_tick = efx_a_end_tick + 10
        log(string.format("Writing EFX B starting at tick %d...", efx_b_type_tick))
        insert_event(efx_b_type_tick, 0x40, 0x04, 0x00, target_efx_b.type_v1, target_efx_b.type_v2, true)
        
        current_tick = efx_b_type_tick
        for _, p in ipairs(target_efx_b.params) do
            current_tick = current_tick + 10
            insert_event(current_tick, 0x40, 0x04, p.offset, p.v1, p.v2, false)
        end
        local last_efx_b_param_tick = current_tick
        
        -- EFX B Send (固定スロット: efx_b_type_tick + 80)
        local efx_b_send_rev_tick = math.max(last_efx_b_param_tick + 10, efx_b_type_tick + 80)
        insert_event(efx_b_send_rev_tick,      0x40, 0x04, 0x17, target_efx_b.send_rev, 0, false)
        insert_event(efx_b_send_rev_tick + 10, 0x40, 0x04, 0x18, target_efx_b.send_cho, 0, false)
        insert_event(efx_b_send_rev_tick + 20, 0x40, 0x04, 0x19, target_efx_b.send_del, 0, false)
        efx_b_end_tick = efx_b_send_rev_tick + 20
    end
    
    -- 5. Part Assign (R02規格規約: EFX末尾から+10 tick空けて順次配置)
    if #existing_part_assign > 0 then
        local part_assign_start_tick = efx_b_end_tick + 10
        log(string.format("Writing Part Assign starting at tick %d (%d events)...", part_assign_start_tick, #existing_part_assign))
        current_tick = part_assign_start_tick
        for idx, pa in ipairs(existing_part_assign) do
            insert_event(current_tick, 0x40, pa.sub, pa.offset, pa.v1, pa.v2, false)
            current_tick = current_tick + 10
        end
    end
    
    reaper.MIDI_Sort(take)
    midi_utils.on_edit("EFX の種類を変更")
    log(string.rep("=", 80))
    log(string.format("COMPLETE: Final tick position = %d", current_tick))
    log(string.rep("=", 80))
    
    if log_file then
        log_file:close()
    end
end


-- ============================================================================
-- Amp Type Small Disabled Helper
-- Distortion / Overdrive 系の Amp Type で Small (0) を選択禁止にする判定
-- ============================================================================
function is_amp_type_small_disabled(param)
    if not param then return false end
    local type_info = param.type_info
    if type_info and type_info.type == "discrete" and type_info.choices then
        if type_info.choices[1] == "Small" then
            return true
        end
    end
    return false
end


return {TRACK_TO_SUB = TRACK_TO_SUB, SUB_TO_TRACK = SUB_TO_TRACK, BUTTON_ORDER = BUTTON_ORDER,
        midi_utils = midi_utils, live = live}
