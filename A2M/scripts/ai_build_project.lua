-- @description AI でプロジェクトを作る（テンポマップ・ステム・下書き・コードを一度に）
-- @version 1.4.0
--
-- 使い方：原曲の音声アイテムを1つ選んで、このスクリプトを実行する。
--   画面で「ピアノを分ける」「ギターを分ける」と、分離の重ね（2 / 4 / 8 / 16。既定は 8）を選んで「実行」。
--   基本はドラム / ベース / その他 / 歌 の4つに分ける。ピアノとギターは選んだときだけ別にする。
-- やること（今開いているプロジェクトに直接入れる。元に戻すは Ctrl+Z で一度に戻る）：
--   1. テンポマップ（整数BPM。揺れる曲は小節頭でBPMを変える）。曲の頭の前に、セットアップ用の空き小節をとる
--   2. 選んだ音声アイテム（原曲）を「2MIX」トラックへ動かし、テンポマップに合う位置に置いて、アイテムミュートする
--   3. 「2MIX」フォルダの中のトラックに、分離したステムを入れ、トラック名を変える
--   4. 「A10R Drums」「A02 Bass」は、テンプレートのセットアップ（エクスクルーシブ）のアイテムに下書きをくっつける。
--      「A04 Vocal」は下書きのアイテムを別に入れる
--   5. 「コード」トラック（「Other TEMPLATE」の上。無ければ作り、別の場所にあれば動かす。ミュート）に、
--      コードの構成音を塗りつぶした MIDI を入れる
-- トラックは名前で探す（位置は見ない）。もう一度実行すると、前にこのスクリプトが入れた物だけを消してから入れ直す。
-- （ドラムとベースはセットアップとくっついているので、もう一度実行すると、そのアイテムの音符を入れ直す。確認が出る）
-- 重い処理は Python（scripts/reaper_backend.py）が裏で行う。数分かかる。その間も Reaper は使える。

local TITLE = "AI でプロジェクトを作る"
local EXT = "A2M_AI"                         -- プロジェクトに覚えておく情報の名前
local DRAFT_PREFIX = "AI下書き"             -- config.yaml の m4.draft.item_prefix と同じ
local STEM_TAKE_PREFIX = "AI ステム "
local MIX_FOLDER_NAME = "2MIX"
local DELIVERY_FIRST = "A10R Drums"
local MERGE_TRACKS = {["A10R Drums"] = true, ["A02 Bass"] = true}  -- セットアップのアイテムにくっつけるトラック（norm した名前）
local CMD_GLUE = 40362          -- 項目: アイテムをくっつける（時間選択を無視）
local CMD_BUILD_PEAKS = 40047   -- ピーク: 足りないピークを作る
local OVERLAP_CHOICES = {2, 4, 8, 16}  -- config.yaml の separation.overlap_choices と同じ
local DEFAULT_OVERLAP = 8               -- config.yaml の separation.overlap_listen と同じ

---------------------------------------------------------------------------
-- 場所
---------------------------------------------------------------------------
local function script_dir()
    local src = debug.getinfo(1, "S").source
    return src:match([[^@?(.*[\/])[^\/]-$]]) or ""
end

local function repo_root()
    local s = script_dir()
    local parent = s:gsub([[scripts[\/]$]], "")
    if reaper.file_exists(parent .. "scripts\\reaper_backend.py") then return parent end
    return s
end

local ROOT = repo_root()
local JOB_DIR = ROOT .. "scratch\\reaper_job\\"   -- config.yaml の reaper.job_dir と同じ
-- 配布（ZIP）では A2M フォルダの python\python.exe（Python と部品を丸ごと入れたもの）。開発では .venv
local PYTHON = reaper.file_exists(ROOT .. "python\\python.exe") and (ROOT .. "python\\python.exe") or (ROOT .. ".venv\\Scripts\\python.exe")
local BACKEND = ROOT .. "scripts\\reaper_backend.py"

---------------------------------------------------------------------------
-- 小さな道具
---------------------------------------------------------------------------
local function split_tab(line)
    local out = {}
    for field in (line .. "\t"):gmatch("([^\t]*)\t") do out[#out + 1] = field end
    return out
end

local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end

local function write_file(path, text)
    local f = io.open(path, "wb")
    if not f then return false end
    f:write(text)
    f:close()
    return true
end

local function norm(name)
    -- トラック名の比べ方：前後の空白を取り、続く空白を1つにする（"A02  Bass" と "A02 Bass" を同じとみなす）
    return (name or ""):gsub("^%s+", ""):gsub("%s+$", ""):gsub("%s+", " ")
end

local function track_name(tr)
    local _, name = reaper.GetTrackName(tr)
    return name
end

local function find_track(name)
    local want = norm(name)
    for i = 0, reaper.CountTracks(0) - 1 do
        local tr = reaper.GetTrack(0, i)
        if norm(track_name(tr)) == want then return tr, i end
    end
    return nil
end

local function take_name(item)
    local take = reaper.GetActiveTake(item)
    if not take then return "" end
    local _, n = reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", false)
    return n
end

local function delete_items_where(tr, pred)
    for i = reaper.CountTrackMediaItems(tr) - 1, 0, -1 do
        local item = reaper.GetTrackMediaItem(tr, i)
        if pred(item) then reaper.DeleteTrackMediaItem(tr, item) end
    end
end

local function starts_with(s, prefix)
    return s:sub(1, #prefix) == prefix
end

local function item_guid(item)
    local _, g = reaper.GetSetMediaItemInfo_String(item, "GUID", "", false)
    return g
end

local function find_item_by_guid(tr, guid)
    if not guid or guid == "" then return nil end
    for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
        local item = reaper.GetTrackMediaItem(tr, i)
        if item_guid(item) == guid then return item end
    end
    return nil
end

local function note_count(item)
    local take = reaper.GetActiveTake(item)
    if not take or not reaper.TakeIsMIDI(take) then return 0 end
    local _, notes = reaper.MIDI_CountEvts(take)
    return notes
end

local function insert_audio_file(tr, path)
    -- Reaper にファイルをドラッグしたのと同じ入れ方（波形の表示データも作られる）
    local before = {}
    for i = 0, reaper.CountTrackMediaItems(tr) - 1 do before[reaper.GetTrackMediaItem(tr, i)] = true end
    reaper.SetOnlyTrackSelected(tr)
    reaper.SetEditCurPos(0, false, false)
    local idx = math.floor(reaper.GetMediaTrackInfo_Value(tr, "IP_TRACKNUMBER")) - 1
    reaper.InsertMedia(path, 512 | (idx << 16))  -- 512：上位の値をトラックの番号（0始まり）として使う
    for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
        local item = reaper.GetTrackMediaItem(tr, i)
        if not before[item] then return item end
    end
    return nil
end

---------------------------------------------------------------------------
-- 2MIX のフォルダの中のトラック
---------------------------------------------------------------------------
local function mix_folder_tracks()
    local mix, idx = find_track(MIX_FOLDER_NAME)
    if not mix then return nil, "「" .. MIX_FOLDER_NAME .. "」という名前のトラックがありません。" end
    local out = {}
    local n = reaper.CountTracks(0)
    if reaper.GetMediaTrackInfo_Value(mix, "I_FOLDERDEPTH") == 1 then
        -- 2MIX がフォルダの親：その直下のトラック
        local level = 1
        for i = idx + 1, n - 1 do
            local tr = reaper.GetTrack(0, i)
            if level == 1 then out[#out + 1] = tr end
            level = level + reaper.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH")
            if level <= 0 then break end
        end
    else
        -- 2MIX がフォルダの中の1本：同じフォルダの、後ろのトラック
        local level = 0
        level = level + reaper.GetMediaTrackInfo_Value(mix, "I_FOLDERDEPTH")
        for i = idx + 1, n - 1 do
            if level < 0 then break end
            local tr = reaper.GetTrack(0, i)
            if norm(track_name(tr)) == norm(DELIVERY_FIRST) then break end
            if level == 0 then out[#out + 1] = tr end
            level = level + reaper.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH")
        end
    end
    if #out == 0 then return nil, "「" .. MIX_FOLDER_NAME .. "」のフォルダの中にトラックがありません。" end
    return out
end

---------------------------------------------------------------------------
-- 結果をプロジェクトに入れる
---------------------------------------------------------------------------
local function place_audio(item, take, pos, offset, length)
    -- length は原曲の長さ（裏方が音源を読んで測った値）。Reaper から取ると 0 になることがあったので使わない
    reaper.SetMediaItemInfo_Value(item, "D_POSITION", pos)
    reaper.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", offset)
    reaper.SetMediaItemInfo_Value(item, "D_LENGTH", length - offset)
end

local function put_notes(take, channel, notes, ppq)
    for _, n in ipairs(notes) do
        local s = reaper.MIDI_GetPPQPosFromProjQN(take, n[1] / ppq)
        local e = reaper.MIDI_GetPPQPosFromProjQN(take, n[2] / ppq)
        reaper.MIDI_InsertNote(take, false, false, s, e, channel - 1, n[3], n[4], true)
    end
    reaper.MIDI_Sort(take)
end

local function add_midi_item(tr, name, channel, length_s, notes, ppq)
    local item = reaper.CreateNewMIDIItemInProj(tr, 0, length_s, false)
    local take = reaper.GetActiveTake(item)
    reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", name, true)
    put_notes(take, channel, notes, ppq)
    return item
end

local function setup_item(tr, except)
    -- テンプレートのセットアップのアイテム：位置が 0 以上で一番早い MIDI アイテム（このスクリプトが入れた物は除く）
    local best, best_pos = nil, math.huge
    for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
        local item = reaper.GetTrackMediaItem(tr, i)
        local take = reaper.GetActiveTake(item)
        local pos = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
        if item ~= except and take and reaper.TakeIsMIDI(take) and pos >= -0.000001 and pos < best_pos
            and not starts_with(take_name(item), DRAFT_PREFIX) then
            best, best_pos = item, pos
        end
    end
    return best
end

local function merge_draft(tr, m, ppq)
    -- 下書きを、テンプレートのセットアップのアイテムとくっつける。どちらも拍の位置で持っているので、位置はずれない。
    local key = "merged:" .. norm(m.track)
    local _, guid = reaper.GetProjExtState(0, EXT, key)
    local merged = find_item_by_guid(tr, guid)
    if merged then
        -- 前にくっつけたアイテム：音符だけ入れ直す（セットアップの CC やエクスクルーシブはそのまま）
        local take = reaper.GetActiveTake(merged)
        for i = note_count(merged) - 1, 0, -1 do reaper.MIDI_DeleteNote(take, i) end
        put_notes(take, m.channel, m.notes, ppq)
        local pos = reaper.GetMediaItemInfo_Value(merged, "D_POSITION")
        local len = reaper.GetMediaItemInfo_Value(merged, "D_LENGTH")
        reaper.SetMediaItemInfo_Value(merged, "D_LENGTH", math.max(len, m.length - pos))
        return merged
    end
    local draft = add_midi_item(tr, m.name, m.channel, m.length, m.notes, ppq)
    local setup = setup_item(tr, draft)
    if not setup then return draft end  -- セットアップのアイテムが無ければ、下書きのまま
    local _, setup_name = reaper.GetSetMediaItemTakeInfo_String(reaper.GetActiveTake(setup), "P_NAME", "", false)
    reaper.SelectAllMediaItems(0, false)
    reaper.SetMediaItemSelected(setup, true)
    reaper.SetMediaItemSelected(draft, true)
    reaper.Main_OnCommand(CMD_GLUE, 0)
    merged = reaper.GetSelectedMediaItem(0, 0)
    if merged then
        reaper.GetSetMediaItemTakeInfo_String(reaper.GetActiveTake(merged), "P_NAME", setup_name .. " ＋ AI", true)
        reaper.SetProjExtState(0, EXT, key, item_guid(merged))
    end
    return merged
end

local function delete_old_regions()
    local _, saved = reaper.GetProjExtState(0, EXT, "regions")
    if saved == "" then return end
    local want = {}
    for idx, name in saved:gmatch("(%d+)=([^\n]*)\n") do want[tonumber(idx)] = name end
    local i = 0
    local found = {}
    while true do
        local ret, isrgn, _, _, name, idx = reaper.EnumProjectMarkers3(0, i)
        if ret == 0 then break end
        if isrgn and want[idx] == name then found[#found + 1] = idx end
        i = i + 1
    end
    for _, idx in ipairs(found) do reaper.DeleteProjectMarker(0, idx, true) end
    reaper.SetProjExtState(0, EXT, "regions", "")
end

local function apply_result(text, sel_item, sel_take)
    local ppq = 480
    local audio, tempos, stems, midis, summary = nil, {}, {}, {}, {}
    local current = nil
    for line in text:gmatch("[^\r\n]+") do
        local f = split_tab(line)
        local kind = f[1]
        if kind == "PPQ" then ppq = tonumber(f[2])
        elseif kind == "AUDIO" then audio = {tonumber(f[2]), tonumber(f[3]), tonumber(f[4])}
        elseif kind == "TEMPO" then tempos[#tempos + 1] = {tonumber(f[2]), tonumber(f[3]), tonumber(f[4]), tonumber(f[5])}
        elseif kind == "STEM" then stems[#stems + 1] = {tonumber(f[2]), f[3], f[4]}
        elseif kind == "MIDI" or kind == "CHORDTRACK" then
            current = {kind = kind, track = f[2], name = f[3], channel = tonumber(f[4]), length = tonumber(f[5]), notes = {}}
            if kind == "CHORDTRACK" then current.before = f[3]; current.name = f[2]; current.channel = tonumber(f[4]); current.length = tonumber(f[5]) end
            midis[#midis + 1] = current
        elseif kind == "N" and current then
            current.notes[#current.notes + 1] = {tonumber(f[2]), tonumber(f[3]), tonumber(f[4]), tonumber(f[5])}
        elseif kind == "SUMMARY" then summary[#summary + 1] = f[2]
        end
    end
    if not audio or not audio[3] or #tempos == 0 then return nil, "結果が読めませんでした（裏方とスクリプトの版が合っていないかもしれません）。" end

    -- 先に入れ先のトラックがそろっているかを確かめる（途中で止まらないように）
    local mix_track = find_track(MIX_FOLDER_NAME)
    local folder, err = mix_folder_tracks()
    if not folder then return nil, err end
    if #folder < #stems then
        return nil, "「" .. MIX_FOLDER_NAME .. "」のフォルダの中のトラックが足りません（ステム " .. #stems .. " 本、トラック " .. #folder .. " 本）。"
    end
    for _, m in ipairs(midis) do
        if m.kind == "MIDI" and not find_track(m.track) then return nil, "「" .. m.track .. "」という名前のトラックがありません。" end
        if m.kind == "CHORDTRACK" and not find_track(m.before) then return nil, "「" .. m.before .. "」という名前のトラックがありません。" end
    end
    -- ドラムとベースを入れ直すときは、手で直した音符も消えるので確かめる
    local redo = {}
    for _, m in ipairs(midis) do
        local tr = m.kind == "MIDI" and MERGE_TRACKS[norm(m.track)] and find_track(m.track)
        if tr then
            local _, guid = reaper.GetProjExtState(0, EXT, "merged:" .. norm(m.track))
            local merged = find_item_by_guid(tr, guid)
            if merged and note_count(merged) > 0 then redo[#redo + 1] = m.track end
        end
    end
    local skip_merge = false
    if #redo > 0 then
        local ans = reaper.ShowMessageBox(table.concat(redo, "、") .. " の音符を、新しい下書きに入れ直します。\n手で直した音符も消えます。入れ直しますか？\n\n「いいえ」なら、ドラムとベースはそのまま残して、ほかを入れ直します。", TITLE, 4)
        skip_merge = ans ~= 6
    end

    reaper.Undo_BeginBlock()
    reaper.PreventUIRefresh(1)

    -- 1. テンポマップ（前のテンポ・拍子の点は全部消す）
    for i = reaper.CountTempoTimeSigMarkers(0) - 1, 0, -1 do reaper.DeleteTempoTimeSigMarker(0, i) end
    for _, t in ipairs(tempos) do
        reaper.SetTempoTimeSigMarker(0, -1, t[1], -1, -1, t[2], t[3], t[4], false)
    end
    reaper.UpdateTimeline()

    -- 2. 原曲を 2MIX トラックへ動かし、テンポマップに合う位置に置く（先にどかしてから、ステムを入れる）
    if reaper.GetMediaItem_Track(sel_item) ~= mix_track then reaper.MoveMediaItemToTrack(sel_item, mix_track) end
    place_audio(sel_item, sel_take, audio[1], audio[2], audio[3])
    reaper.SetMediaItemInfo_Value(sel_item, "B_MUTE", 1)

    -- 3. ステム（2MIX のフォルダの中。前にこのスクリプトが入れたステムは消す）
    for _, tr in ipairs(folder) do
        delete_items_where(tr, function(it) return starts_with(take_name(it), STEM_TAKE_PREFIX) end)
    end
    local cursor = reaper.GetCursorPosition()
    for _, s in ipairs(stems) do
        local tr = folder[s[1] + 1]
        local item = insert_audio_file(tr, s[3])
        if not item then
            reaper.PreventUIRefresh(-1)
            reaper.Undo_EndBlock(TITLE, -1)
            return nil, "ステムを入れられませんでした: " .. s[3]
        end
        local take = reaper.GetActiveTake(item)
        reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", STEM_TAKE_PREFIX .. s[2], true)
        place_audio(item, take, audio[1], audio[2], audio[3])
        reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", s[2], true)
    end
    reaper.SetEditCurPos(cursor, false, false)

    -- 4・5. 下書きの MIDI と、コードのトラック
    for _, m in ipairs(midis) do
        if m.kind == "MIDI" then
            local tr = find_track(m.track)
            local merge = MERGE_TRACKS[norm(m.track)]
            if not (merge and skip_merge) then
                delete_items_where(tr, function(it) return starts_with(take_name(it), DRAFT_PREFIX) end)
                if merge then merge_draft(tr, m, ppq) else add_midi_item(tr, m.name, m.channel, m.length, m.notes, ppq) end
            end
        else
            local tr, idx = find_track(m.track)
            local _, before = find_track(m.before)
            if not tr then
                reaper.InsertTrackAtIndex(before, true)
                tr = reaper.GetTrack(0, before)
                reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", m.track, true)
            elseif idx ~= before - 1 then
                -- 別の場所にあれば、中身ごと「Other TEMPLATE」の上へ動かす
                reaper.SetOnlyTrackSelected(tr)
                reaper.ReorderSelectedTracks(before, 0)
            end
            reaper.SetMediaTrackInfo_Value(tr, "B_MUTE", 1)
            delete_items_where(tr, function(it) return take_name(it) == m.track end)
            add_midi_item(tr, m.track, m.channel, m.length, m.notes, ppq)
        end
    end

    -- 前の版が入れたコードのリージョンを消す（コードのリージョンはやめた）
    delete_old_regions()

    -- 選びを元に戻し、波形の表示データを作る
    reaper.SelectAllMediaItems(0, false)
    reaper.SetMediaItemSelected(sel_item, true)
    reaper.SetOnlyTrackSelected(mix_track)
    reaper.Main_OnCommand(CMD_BUILD_PEAKS, 0)

    reaper.PreventUIRefresh(-1)
    reaper.UpdateArrange()
    reaper.Undo_EndBlock(TITLE, -1)
    return summary
end

---------------------------------------------------------------------------
-- 画面
---------------------------------------------------------------------------
local W, H = 520, 340
local state = "choose"          -- choose → running → done / failed
local opt = {piano = false, guitar = false, overlap = DEFAULT_OVERLAP}
local sel_item, sel_take, audio_path
local message, detail = "", {}
local pct = 0
local started = 0
local last_poll = 0
local mouse_was_down = false

local function layout_name()
    if opt.piano and opt.guitar then return "6" end
    if opt.piano then return "piano" end
    if opt.guitar then return "guitar" end
    return "4"
end

local function start_backend()
    reaper.RecursiveCreateDirectory(JOB_DIR, 0)
    for _, f in ipairs({"result.txt", "error.txt", "progress.txt"}) do os.remove(JOB_DIR .. f) end
    local media = reaper.GetProjectPath("")
    local name = audio_path:match("([^\\/]+)$"):gsub("%.[^%.]+$", "")
    local req = "audio\t" .. audio_path .. "\nlayout\t" .. layout_name() .. "\noverlap\t" .. opt.overlap
        .. "\nmedia\t" .. media .. "\nname\t" .. name .. "\n"
    if not write_file(JOB_DIR .. "request.txt", req) then return false, "作業フォルダに書けません: " .. JOB_DIR end
    if not reaper.file_exists(PYTHON) then return false, "Python が見つかりません: " .. PYTHON end
    local cmd = string.format('"%s" "%s" "%s"', PYTHON, BACKEND, JOB_DIR:gsub("\\$", ""))
    reaper.ExecProcess(cmd, -1)  -- 待たずに裏で動かす
    started = reaper.time_precise()
    return true
end

local function poll()
    local now = reaper.time_precise()
    if now - last_poll < 0.5 then return end
    last_poll = now
    local p = read_file(JOB_DIR .. "progress.txt")
    if p then
        local a, b = p:match("^(%d+)\t(.*)$")
        if a then pct, message = tonumber(a), b end
    end
    local err = read_file(JOB_DIR .. "error.txt")
    if err then
        state, message = "failed", "失敗しました"
        detail = {err, "詳しくは " .. JOB_DIR .. "log.txt"}
        return
    end
    local res = read_file(JOB_DIR .. "result.txt")
    if res then
        local summary, e = apply_result(res, sel_item, sel_take)
        if summary then
            state, message, detail = "done", "できました", summary
        else
            state, message, detail = "failed", "プロジェクトに入れられませんでした", {e}
        end
    end
end

local function button(x, y, w, h, label, enabled)
    local hover = gfx.mouse_x >= x and gfx.mouse_x <= x + w and gfx.mouse_y >= y and gfx.mouse_y <= y + h
    gfx.set(enabled and (hover and 0.35 or 0.25) or 0.18, enabled and (hover and 0.55 or 0.45) or 0.18, enabled and (hover and 0.85 or 0.75) or 0.18, 1)
    gfx.rect(x, y, w, h, 1)
    gfx.set(1, 1, 1, enabled and 1 or 0.4)
    local tw, th = gfx.measurestr(label)
    gfx.x, gfx.y = x + (w - tw) / 2, y + (h - th) / 2
    gfx.drawstr(label)
    return hover and enabled
end

local function checkbox(x, y, label, on)
    local hover = gfx.mouse_x >= x and gfx.mouse_x <= x + 240 and gfx.mouse_y >= y and gfx.mouse_y <= y + 22
    gfx.set(0.85, 0.85, 0.85, 1)
    gfx.rect(x, y + 2, 18, 18, 0)
    if on then gfx.set(0.4, 0.75, 1, 1); gfx.rect(x + 4, y + 6, 10, 10, 1) end
    gfx.set(1, 1, 1, 1)
    gfx.x, gfx.y = x + 28, y
    gfx.drawstr(label)
    return hover
end

-- 小さな選択ボタン（選ばれているものは色が付く）。マウスが乗っていれば true
local function choice(x, y, w, label, on)
    local hover = gfx.mouse_x >= x and gfx.mouse_x <= x + w and gfx.mouse_y >= y and gfx.mouse_y <= y + 26
    if on then gfx.set(0.4, 0.75, 1, 1) else gfx.set(hover and 0.35 or 0.25, hover and 0.35 or 0.25, hover and 0.4 or 0.3, 1) end
    gfx.rect(x, y, w, 26, 1)
    gfx.set(on and 0 or 1, on and 0 or 1, on and 0 or 1, 1)
    local tw, th = gfx.measurestr(label)
    gfx.x, gfx.y = x + (w - tw) / 2, y + (26 - th) / 2
    gfx.drawstr(label)
    return hover
end

local function text(x, y, s, r, g, b)
    gfx.set(r or 0.9, g or 0.9, b or 0.9, 1)
    gfx.x, gfx.y = x, y
    gfx.drawstr(s)
end

local function frame()
    local ch = gfx.getchar()
    if ch < 0 then return end  -- 閉じた
    local down = gfx.mouse_cap & 1 == 1
    local clicked = mouse_was_down and not down
    mouse_was_down = down

    gfx.set(0.13, 0.13, 0.15, 1)
    gfx.rect(0, 0, W, H, 1)
    gfx.setfont(1)
    text(20, 16, TITLE, 1, 1, 1)
    gfx.setfont(2)
    text(20, 48, "音源: " .. (audio_path:match("([^\\/]+)$") or audio_path))

    if state == "choose" then
        text(20, 80, "分ける楽器（基本はドラム / ベース / その他 / 歌）")
        local hp = checkbox(30, 110, "ピアノを分ける", opt.piano)
        local hg = checkbox(30, 140, "ギターを分ける", opt.guitar)
        text(20, 180, "分離の重ね（大きいほどきれい。時間は比例して長い）")
        local ho, x = nil, 30
        for _, n in ipairs(OVERLAP_CHOICES) do
            local label = n == DEFAULT_OVERLAP and (n .. "（既定）") or tostring(n)
            local w = n == DEFAULT_OVERLAP and 90 or 50
            if choice(x, 208, w, label, opt.overlap == n) then ho = n end
            x = x + w + 10
        end
        local hr = button(W - 250, H - 60, 110, 36, "実行", true)
        local hc = button(W - 130, H - 60, 110, 36, "キャンセル", true)
        local run = ch == 13
        if clicked then
            if hp then opt.piano = not opt.piano end
            if hg then opt.guitar = not opt.guitar end
            if ho then opt.overlap = ho end
            if hc then gfx.quit(); return end
            run = run or hr
        end
        if run then
            local ok, e = start_backend()
            if ok then state, message = "running", "始めています"
            else state, message, detail = "failed", "始められませんでした", {e} end
        end
        if ch == 27 then gfx.quit(); return end
    elseif state == "running" then
        poll()
        text(20, 90, message)
        gfx.set(0.25, 0.25, 0.28, 1); gfx.rect(20, 125, W - 40, 18, 1)
        gfx.set(0.4, 0.75, 1, 1); gfx.rect(20, 125, (W - 40) * pct / 100, 18, 1)
        local sec = math.floor(reaper.time_precise() - started)
        text(20, 155, string.format("%d%%　経過 %d分%02d秒（数分かかります）", pct, sec // 60, sec % 60), 0.7, 0.7, 0.7)
        text(20, 185, "この画面を閉じると、結果はプロジェクトに入りません。", 0.7, 0.7, 0.7)
    else
        local ok = state == "done"
        text(20, 80, message, ok and 0.6 or 1, ok and 1 or 0.5, ok and 0.6 or 0.5)
        local y = 110
        for _, line in ipairs(detail) do text(30, y, line); y = y + 24 end
        if button(W - 130, H - 60, 110, 36, "閉じる", true) and clicked then gfx.quit(); return end
        if ch == 13 or ch == 27 then gfx.quit(); return end
    end
    gfx.update()
    reaper.defer(frame)
end

local function main()
    if reaper.CountSelectedMediaItems(0) ~= 1 then
        reaper.ShowMessageBox("原曲の音声アイテムを1つ選んでから実行してください。", TITLE, 0)
        return
    end
    sel_item = reaper.GetSelectedMediaItem(0, 0)
    sel_take = reaper.GetActiveTake(sel_item)
    if not sel_take or reaper.TakeIsMIDI(sel_take) then
        reaper.ShowMessageBox("選んだアイテムが音声ではありません。", TITLE, 0)
        return
    end
    local src = reaper.GetMediaItemTake_Source(sel_take)
    local parent = reaper.GetMediaSourceParent(src)
    if parent then src = parent end
    audio_path = reaper.GetMediaSourceFileName(src, "")
    if audio_path == "" or not reaper.file_exists(audio_path) then
        reaper.ShowMessageBox("音声のファイルが見つかりません。", TITLE, 0)
        return
    end
    local _, err = mix_folder_tracks()
    if err then reaper.ShowMessageBox(err, TITLE, 0); return end
    if not find_track(DELIVERY_FIRST) then
        reaper.ShowMessageBox("「" .. DELIVERY_FIRST .. "」という名前のトラックがありません。", TITLE, 0)
        return
    end
    gfx.init(TITLE, W, H)
    gfx.setfont(1, "Meiryo UI", 22, string.byte("b"))
    gfx.setfont(2, "Meiryo UI", 17)
    frame()
end

main()
