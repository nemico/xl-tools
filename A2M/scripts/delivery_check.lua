-- @description 納品チェック (JOYSOUND SMF R02Checker & NTVT 一発検証)
-- @version 1.1.0
-- @author Antigravity pair programming
--
-- 【仕様 Card P3-2 準拠】
-- 1. A10R Drums から A01 SYSTEM_EXCLUSIVE までの 17トラックを自動抽出
-- 2. 上部の音源(Original等)や下部の余分なテンプレートは除外
-- 3. PPQ 480 でダイアログ無しで SMF Format 1 書き出し (ReaScript API)
-- 4. ntvt_equiv による NTVT 変換 (Port C, コメント挿入, CC整列)
-- 5. R02Checker による実機チェック
-- 6. ログ解析 & 位置 (秒) 算出
-- 7. 合格時: 「エラー0・警告0」と表示
-- 8. 不備時:
--    - 既存の "!CHK: " マーカーのみを削除（ユーザーマーカーは保持）
--    - エラー位置に "!CHK: <ルール名>" の赤色マーカーを配置
--    - エラー箇所の該当トラックを選択 (start_track_idx からのオフセット対応)
--    - reports/delivery/ にレポート書き出し & 件数表示
-- ※ プロジェクトは保存しない。トラック色は変更しない。Reaper設定は触らない。

local function get_script_path()
    local info = debug.getinfo(1, "S")
    local script_path = info.source:match([[^@?(.*[\/])[^\/]-$]])
    return script_path or ""
end

-- Helper to find package root
local function find_workspace_root()
    local s_path = get_script_path()
    -- 1. If script is in scripts/, root is parent folder
    local parent = s_path:gsub([[scripts[\\/]$]], "")
    if reaper.file_exists(parent .. "MIDIChecker\\R02Checker.exe") then
        return parent
    end
    -- 2. If script is directly inside package folder
    if reaper.file_exists(s_path .. "MIDIChecker\\R02Checker.exe") then
        return s_path
    end
    -- 3. Fallback to project path
    local proj_path = reaper.GetProjectPath("") .. "\\"
    if reaper.file_exists(proj_path .. "MIDIChecker\\R02Checker.exe") then
        return proj_path
    end
    return parent ~= "" and parent or s_path
end

local WORKSPACE_ROOT = find_workspace_root()
-- Python：配布（ZIP）では A2M フォルダの python\python.exe（Python と mido を丸ごと入れたもの）。開発では .venv
local function find_python()
    for _, p in ipairs({WORKSPACE_ROOT .. "python\\python.exe", WORKSPACE_ROOT .. ".venv\\Scripts\\python.exe"}) do
        if reaper.file_exists(p) then return p end
    end
    return nil
end
-- 足りないもの（MIDIChecker・Python）があれば、知らせる文。揃っていれば nil
local function missing_parts()
    if not reaper.file_exists(WORKSPACE_ROOT .. "MIDIChecker\\R02Checker.exe") or not find_python() then
        return "納品チェックには、MIDIChecker（R02Checker）と Python が要ります。\n" ..
            "全部入りの ZIP を、XLabenTools フォルダに入れてから「XLaben_Update」を実行してください（自動で入ります）。\n\n" ..
            "探した場所：" .. WORKSPACE_ROOT
    end
    return nil
end
local WORK_DIR = WORKSPACE_ROOT .. "scratch\\reaper_delivery_check\\"
local REPORT_DIR = WORKSPACE_ROOT .. "reports\\delivery\\"

-- Helper to encode Variable-Length Quantity (VLQ) for MIDI
local function encode_vlq(num)
    local s = string.char(num & 0x7F)
    num = num >> 7
    while num > 0 do
        s = string.char((num & 0x7F) | 0x80) .. s
        num = num >> 7
    end
    return s
end

local function u32(num)
    return string.pack(">I4", num)
end

local function u16(num)
    return string.pack(">I2", num)
end

-- Find the first track matching A10R (e.g. "A10R Drums")
local function find_delivery_tracks()
    local total_tracks = reaper.CountTracks(0)
    local start_idx = nil

    for i = 0, total_tracks - 1 do
        local tr = reaper.GetTrack(0, i)
        local _, name = reaper.GetTrackName(tr)
        -- Must start with A10R (e.g. "A10R Drums" or "A10R")
        -- Skip anything like "StartEnka" or template labels
        if name:match("^A10R") then
            start_idx = i
            break
        end
    end

    if not start_idx then
        return nil, "打ち込み対象の先頭トラック（A10R Drums）が見つかりませんでした。\nトラック名が 'A10R Drums' 等で始まっているか確認してください。"
    end

    if start_idx + 16 >= total_tracks then
        return nil, string.format("A10R Drums (Track %d) 以降に 17 トラック分の領域がありません (全 %d トラック)。", start_idx + 1, total_tracks)
    end

    return start_idx, nil
end

-- Assemble SMF Format 1 from project tracks without modal dialogs
local function export_project_midi_silent(target_path, start_track_idx)
    -- 1. Track 0 (Conductor track)
    local t0_data = ""
    local proj_name = ""
    local _, prj = reaper.EnumProjects(-1)
    if prj and prj ~= "" then
        proj_name = prj:match("([^\\/]+)%.rpp$") or ""
    end
    -- Try finding 6-digit VD code in project name
    local vd_num = proj_name:match("(%d%d%d%d%d%d)") or "000000"
    local vd_code = "VD" .. vd_num

    -- Add sequence/track name meta event (00 FF 03 len text)
    t0_data = t0_data .. "\x00\xFF\x03" .. string.char(#vd_code) .. vd_code

    -- Tempo & Time Signatures from Reaper Tempo map
    local marker_count = reaper.CountTempoTimeSigMarkers(0)
    local last_tick = 0
    if marker_count > 0 then
        for i = 0, marker_count - 1 do
            local retval, timepos, measurepos, beatpos, bpm, timesig_num, timesig_denom, lineartempo = reaper.GetTempoTimeSigMarker(0, i)
            local qn = reaper.TimeMap2_timeToQN(0, timepos)
            local tick = math.floor(qn * 480 + 0.5)
            local delta = math.max(0, tick - last_tick)
            last_tick = tick

            -- Time signature
            if timesig_num and timesig_num > 0 then
                local denom_pow = 2 -- Default 4 -> 2^2
                if timesig_denom == 2 then denom_pow = 1
                elseif timesig_denom == 4 then denom_pow = 2
                elseif timesig_denom == 8 then denom_pow = 3
                elseif timesig_denom == 16 then denom_pow = 4
                end
                t0_data = t0_data .. encode_vlq(delta) .. "\xFF\x58\x04" .. string.char(timesig_num, denom_pow, 24, 8)
                delta = 0
            end

            -- Tempo (microseconds per quarter note)
            if bpm and bpm > 0 then
                local mpqn = math.floor(60000000 / bpm + 0.5)
                local b1 = (mpqn >> 16) & 0xFF
                local b2 = (mpqn >> 8) & 0xFF
                local b3 = mpqn & 0xFF
                t0_data = t0_data .. encode_vlq(delta) .. "\xFF\x51\x03" .. string.char(b1, b2, b3)
            end
        end
    else
        -- Default: 120 BPM, 4/4
        local bpm = reaper.Master_GetTempo()
        if bpm <= 0 then bpm = 120 end
        local mpqn = math.floor(60000000 / bpm + 0.5)
        t0_data = t0_data .. "\x00\xFF\x58\x04\x04\x02\x18\x08"
        t0_data = t0_data .. "\x00\xFF\x51\x03" .. string.char((mpqn >> 16) & 0xFF, (mpqn >> 8) & 0xFF, mpqn & 0xFF)
    end
    -- End of Track
    t0_data = t0_data .. "\x00\xFF\x2F\x00"

    local smf_chunks = {}
    table.insert(smf_chunks, "MTrk" .. u32(#t0_data) .. t0_data)


    -- Priority for ordering events occurring at the exact same tick:
    -- Meta -> SysEx -> CC0 -> CC32 -> CC -> PC -> Bend/Aftertouch -> NoteOff -> NoteOn
    local function get_event_priority(msg)
        local b = string.byte(msg, 1)
        if b == 0xFF then return 1 end
        if b == 0xF0 or b == 0xF7 then return 2 end
        local status = b & 0xF0
        if status == 0xB0 then
            local cc_num = string.byte(msg, 2) or 0
            if cc_num == 0 then return 10 end
            if cc_num == 32 then return 11 end
            return 12
        end
        if status == 0xC0 then return 20 end
        if status == 0xE0 or status == 0xD0 then return 30 end
        if status == 0x80 then return 40 end
        if status == 0x90 then
            local vel = string.byte(msg, 3) or 0
            if vel == 0 then return 40 else return 50 end
        end
        return 60
    end

    -- 2. Target 17 Tracks (start_track_idx .. start_track_idx + 16)
    for rel_idx = 0, 16 do
        local trk_idx = start_track_idx + rel_idx
        local tr = reaper.GetTrack(0, trk_idx)
        local _, trk_name = reaper.GetTrackName(tr)
        trk_name = trk_name or string.format("Track %d", rel_idx + 1)

        local track_events = {}
        local global_order = 0
        local item_count = reaper.CountTrackMediaItems(tr)

        for itm_idx = 0, item_count - 1 do
            local item = reaper.GetTrackMediaItem(tr, itm_idx)
            -- Only include non-muted items!
            if reaper.GetMediaItemInfo_Value(item, "B_MUTE") == 0 then
                local take = reaper.GetActiveTake(item)
                if take and reaper.TakeIsMIDI(take) and reaper.GetMediaItemTakeInfo_Value(take, "B_MUTE") == 0 then
                    local ok, buf = reaper.MIDI_GetAllEvts(take)
                    if ok and buf and #buf > 0 then
                        local pos = 1
                        local buf_len = #buf
                        local cur_take_ppq = 0

                        while pos <= buf_len do
                            local offset, flags, msglen
                            offset, flags, msglen, pos = string.unpack("<i4 B <i4", buf, pos)
                            local raw_msg = string.sub(buf, pos, pos + msglen - 1)
                            pos = pos + msglen
                            cur_take_ppq = cur_take_ppq + offset

                            -- Convert take PPQ position -> project timeline seconds -> QN -> PPQ 480 tick
                            local proj_time = reaper.MIDI_GetProjTimeFromPPQPos(take, cur_take_ppq)
                            local qn = reaper.TimeMap2_timeToQN(0, proj_time)
                            local abs_tick = math.floor(qn * 480 + 0.5)

                            local first_byte = string.byte(raw_msg, 1)
                            local smf_msg = nil

                            if first_byte == 0xFF then
                                -- Meta Event: in Reaper buf, raw_msg is \xFF <type> <payload>
                                local meta_type = string.byte(raw_msg, 2)
                                -- Ignore End-of-Track (0x2F), Sequence/Track Name (0x03), and Port (0x21)
                                if meta_type ~= 0x2F and meta_type ~= 0x03 and meta_type ~= 0x21 then
                                    local payload = string.sub(raw_msg, 3)
                                    smf_msg = "\xFF" .. string.char(meta_type) .. encode_vlq(#payload) .. payload
                                end
                            elseif first_byte == 0xF0 then
                                -- SysEx Event: raw_msg is \xF0 <bytes including F7>
                                -- In SMF: \xF0 <vlq_len> <bytes following F0>
                                local payload = string.sub(raw_msg, 2)
                                smf_msg = "\xF0" .. encode_vlq(#payload) .. payload
                            elseif first_byte == 0xF7 then
                                -- Escaped SysEx Event
                                local payload = string.sub(raw_msg, 2)
                                smf_msg = "\xF7" .. encode_vlq(#payload) .. payload
                            elseif first_byte and first_byte < 0xF0 then
                                -- Channel Voice Message (Note, CC, PC, PitchBend)
                                local status_type = first_byte & 0xF0
                                local is_mode_msg = false
                                if status_type == 0xB0 then
                                    local cc_num = string.byte(raw_msg, 2) or 0
                                    -- Exclude Channel Mode Messages (CC 120..127: All Notes Off, Reset Controllers, etc.)
                                    -- which REAPER take buffers insert at item boundaries and standard SMF export drops
                                    if cc_num >= 120 and cc_num <= 127 then
                                        is_mode_msg = true
                                    end
                                end

                                if is_mode_msg then
                                    smf_msg = nil
                                else
                                    -- Strictly preserve raw MIDI channel! Never force or mutate status bytes.
                                    -- Any channel mismatch (e.g. Tr.5:Ch.1) or voice message on SysEx track
                                    -- must be faithfully exported so R02Checker accurately catches the error.
                                    smf_msg = raw_msg
                                end
                            end

                            if smf_msg then
                                global_order = global_order + 1
                                table.insert(track_events, {
                                    tick = abs_tick,
                                    pri = get_event_priority(smf_msg),
                                    order = global_order,
                                    msg = smf_msg
                                })
                            end
                        end
                    end
                end
            end
        end

        -- Strictly sort all events on this track:
        -- 1. By absolute tick
        -- 2. By event priority (NoteOff strictly precedes NoteOn at the same tick)
        -- 3. By original order (stable sort)
        table.sort(track_events, function(a, b)
            if a.tick ~= b.tick then
                return a.tick < b.tick
            end
            if a.pri ~= b.pri then
                return a.pri < b.pri
            end
            return a.order < b.order
        end)

        -- Build track bytes with variable-length delta times
        local trk_bytes = ""
        local last_tick = 0

        -- 1. Track Name at tick 0
        trk_bytes = trk_bytes .. encode_vlq(0) .. "\xFF\x03" .. string.char(#trk_name) .. trk_name
        -- 2. Port C at tick 0
        trk_bytes = trk_bytes .. encode_vlq(0) .. "\xFF\x21\x01\x02"

        -- 3. Append events
        for _, ev in ipairs(track_events) do
            local delta = math.max(0, ev.tick - last_tick)
            last_tick = ev.tick
            trk_bytes = trk_bytes .. encode_vlq(delta) .. ev.msg
        end

        -- 4. End of Track
        trk_bytes = trk_bytes .. "\x00\xFF\x2F\x00"
        table.insert(smf_chunks, "MTrk" .. u32(#trk_bytes) .. trk_bytes)
    end

    -- Write MThd header (Format 1, 18 tracks: 1 Conductor + 17 Instruments, PPQ 480)
    local header = "MThd" .. u32(6) .. u16(1) .. u16(#smf_chunks) .. u16(480)
    local f = io.open(target_path, "wb")
    if not f then return false end
    f:write(header)
    for _, chunk in ipairs(smf_chunks) do
        f:write(chunk)
    end
    f:close()
    return true
end

-- Clear only !CHK: markers, preserving all user markers
local function clear_chk_markers()
    local i = 0
    while true do
        local retval, isrgn, pos, rgnend, name, markrgnindex = reaper.EnumProjectMarkers(i)
        if retval == 0 then break end
        if not isrgn and string.sub(name, 1, 6) == "!CHK: " then
            reaper.DeleteProjectMarker(0, markrgnindex, false)
        else
            i = i + 1
        end
    end
end

-- Main action execution
local function main()
    local missing = missing_parts()
    if missing then
        reaper.ShowMessageBox(missing, "納品チェック", 0)
        return
    end
    reaper.PreventUIRefresh(1)

    -- Ensure directories exist
    reaper.RecursiveCreateDirectory(WORK_DIR, 0)
    reaper.RecursiveCreateDirectory(REPORT_DIR, 0)

    -- 1. Identify the 17 delivery tracks starting with A10R Drums
    local start_track_idx, err_msg = find_delivery_tracks()
    if not start_track_idx then
        reaper.PreventUIRefresh(-1)
        reaper.ShowMessageBox(err_msg, "納品チェック エラー", 0)
        return
    end

    local target_midi = WORK_DIR .. "reaper_project_export.mid"
    local json_out = WORK_DIR .. "checker_result.json"

    -- Remove stale result json if present
    os.remove(json_out)

    -- 2. Export project MIDI silently (PPQ 480, exactly 17 target tracks)
    local exported = export_project_midi_silent(target_midi, start_track_idx)
    if not exported then
        reaper.PreventUIRefresh(-1)
        reaper.ShowMessageBox("MIDIファイルの書き出しに失敗しました。", "納品チェック エラー", 0)
        return
    end

    -- 3. Run Python delivery_checker backend
    local py_cmd = string.format('"%s"', find_python())
    local script_target
    if reaper.file_exists(WORKSPACE_ROOT .. "delivery_checker.py") then
        script_target = "delivery_checker.py"
    else
        script_target = "-m a2m.delivery_checker"
    end
    local cmd = string.format('cmd /c "cd /d "%s" && %s %s "%s" "%s""', WORKSPACE_ROOT, py_cmd, script_target, target_midi, json_out)

    -- Execute synchronously
    local cmd_output = reaper.ExecProcess(cmd, 15000)

    -- 4. Read result JSON
    local f_json = io.open(json_out, "r")
    if not f_json then
        reaper.PreventUIRefresh(-1)
        local err_detail = cmd_output or "ログファイルが出力されませんでした。"
        reaper.ShowMessageBox(
            "納品チェッカーの実行に失敗しました。\n\n【コマンド出力】\n" .. err_detail .. "\n\nコマンド:\n" .. cmd,
            "納品チェック エラー",
            0
        )
        return
    end
    local json_text = f_json:read("*all")
    f_json:close()

    local passed = json_text:match('"passed":%s*(true)') ~= nil
    local total_errors = tonumber(json_text:match('"total_errors":%s*(%d+)')) or 0
    local total_warnings = tonumber(json_text:match('"total_warnings":%s*(%d+)')) or 0
    local report_path = json_text:match('"report_path":%s*"([^"]+)"') or ""
    report_path = report_path:gsub([[\\]], [[\]])

    -- 5. Clear previous !CHK: markers (never touches user markers)
    clear_chk_markers()

    -- 6. Output report to ReaScript Console
    reaper.ClearConsole()
    local report_content = ""
    local f_rep = io.open(report_path, "r")
    if f_rep then
        report_content = f_rep:read("*all")
        f_rep:close()
    end

    if report_content ~= "" then
        reaper.ShowConsoleMsg(report_content .. "\n")
    else
        if passed then
            reaper.ShowConsoleMsg("【納品チェック合格】エラー 0 件、警告 0 件です。\nJOYSOUND 納品基準を満たしています。\n")
        else
            reaper.ShowConsoleMsg(string.format("【納品チェック結果】エラー: %d 件、警告: %d 件\n", total_errors, total_warnings))
        end
    end

    if passed then
        reaper.PreventUIRefresh(-1)
        reaper.UpdateTimeline()
        return
    end

    -- 7. Errors found: place red markers and select tracks
    -- Deselect all tracks first
    reaper.Main_OnCommand(40297, 0) -- Track: Unselect all tracks

    -- Color Red for markers: ColorToNative(255, 0, 0) | 0x1000000
    local red_color = reaper.ColorToNative(255, 0, 0) | 0x1000000
    local marker_count = 0
    local selected_tracks = {}
    local placed_markers = {}

    -- Parse issues list from JSON text
    for issue_block in json_text:gmatch("{[^{}]+}") do
        local sev = issue_block:match('"severity":%s*"([^"]+)"')
        local rule = issue_block:match('"rule_name":%s*"([^"]+)"')
        local sec = tonumber(issue_block:match('"seconds":%s*([%d%.]+)'))
        local trk_num = tonumber(issue_block:match('"track_num":%s*(%d+)'))

        if sev and rule then
            local marker_name = "!CHK: " .. rule
            local pos = sec or 0.0

            -- Place red marker (deduplicate identical markers at the exact same position)
            local marker_key = string.format("%.3f_%s", pos, marker_name)
            if not placed_markers[marker_key] then
                reaper.AddProjectMarker2(0, false, pos, 0, marker_name, -1, red_color)
                placed_markers[marker_key] = true
                marker_count = marker_count + 1
            end

            -- Select corresponding Reaper track (offset by start_track_idx)
            if trk_num and trk_num >= 1 and trk_num <= 17 then
                local actual_reaper_idx = start_track_idx + (trk_num - 1)
                if not selected_tracks[actual_reaper_idx] then
                    local tr = reaper.GetTrack(0, actual_reaper_idx)
                    if tr then
                        reaper.SetTrackSelected(tr, true)
                        selected_tracks[actual_reaper_idx] = true
                    end
                end
            end
        end
    end

    reaper.PreventUIRefresh(-1)
    reaper.UpdateTimeline()

    -- Append summary notice to console
    local summary_footer = string.format("\n>> 赤色マーカー (!CHK:) を %d 箇所に配置し、該当トラックを選択しました。\n", marker_count)
    reaper.ShowConsoleMsg(summary_footer)
end

main()
