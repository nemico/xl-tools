-- プロジェクトの中を見る・JSFX を入れる（REAPER を使う部分）
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local chase = dofile(DIR .. "xl_chase.lua")
local sysex = dofile(DIR .. "xl_sysex.lua")

local M = {}

M.XLABEN_TRACK = "X-Laben"
M.JS_FILE = "A2M_LivePreview.jsfx"
M.JS_NAME = "A2M Live MIDI preview"
M.GATE_FILE = "A2M_PrerollGate.jsfx"
M.GATE_NAME = "A2M Preroll gate"

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

function M.track_name(tr)
    local _, name = r.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
    return name
end

function M.is_sysex_track_name(name)
    return trim(name):lower():match("^a01[%s_]+system[%s_]*exclusive") ~= nil
end

-- 「A10R Drums」→ 10。A01〜A16 でなければ nil
function M.channel_of(name)
    local n = tonumber(trim(name):match("^A(%d%d?)"))
    if n and n >= 1 and n <= 16 then return n end
end

-- トラック名の「A10R」などを外した短い名前
function M.short_name(name)
    local s = trim(name):gsub("^A%d%d?R?%s*", "")
    return s
end

-- {xlaben = トラック, sysex = トラック, ch = {[1..16] = トラック}}。チャンネルは上から最初に見つかったもの
function M.find_tracks()
    local out = {ch = {}}
    for i = 0, r.CountTracks(0) - 1 do
        local tr = r.GetTrack(0, i)
        local name = M.track_name(tr)
        if trim(name) == M.XLABEN_TRACK then
            out.xlaben = out.xlaben or tr
        elseif M.is_sysex_track_name(name) then
            out.sysex = out.sysex or tr
        else
            local ch = M.channel_of(name)
            if ch and not out.ch[ch] then out.ch[ch] = tr end
        end
    end
    return out
end

-- ------------------------------------------------------------------ JSFX
local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local t = f:read("*a")
    f:close()
    return t
end

local function jsfx_index(tr, js_name)
    for i = 0, r.TrackFX_GetCount(tr) - 1 do
        local _, name = r.TrackFX_GetFXName(tr, i, "")
        if name:find(js_name or M.JS_NAME, 1, true) then return i end
    end
end

-- Effects/A2M/ に file を入れ（中身が変わっていれば書き直す）。書き直したら true
local function install(script_dir, file)
    local src = read_file(script_dir .. file)
    if not src then return nil, "JSFX のファイルが見つかりません: " .. script_dir .. file end
    local dst_dir = r.GetResourcePath() .. "/Effects/A2M/"
    local changed = read_file(dst_dir .. file) ~= src
    if changed then
        r.RecursiveCreateDirectory(dst_dir, 0)
        local f = io.open(dst_dir .. file, "wb")
        if not f then return nil, "Effects フォルダに書けません: " .. dst_dir end
        f:write(src)
        f:close()
    end
    return changed
end

-- マスタートラックの最後に、前から再生するときの音の門（A2M_PrerollGate.jsfx）を置く
function M.ensure_gate(script_dir)
    local changed, err = install(script_dir, M.GATE_FILE)
    if changed == nil then return false, err end
    local master = r.GetMasterTrack(0)
    local idx = jsfx_index(master, M.GATE_NAME)
    if idx and changed then
        r.TrackFX_Delete(master, idx)
        idx = nil
    end
    if not idx then
        idx = r.TrackFX_AddByName(master, "A2M/" .. M.GATE_FILE, false, -1)
        if idx < 0 then return false, "マスタートラックに JSFX を入れられませんでした" end
    end
    return true
end

-- JSFX を Effects/A2M/ に入れ（中身が変わっていれば書き直し）、X-Laben トラックの先頭に置く。
-- script_dir は A2M_LivePreview.jsfx がある場所
function M.ensure_jsfx(xl_track, script_dir)
    local src = read_file(script_dir .. M.JS_FILE)
    if not src then return false, "JSFX のファイルが見つかりません: " .. script_dir .. M.JS_FILE end
    local dst_dir = r.GetResourcePath() .. "/Effects/A2M/"
    local dst = dst_dir .. M.JS_FILE
    local changed = read_file(dst) ~= src
    if changed then
        r.RecursiveCreateDirectory(dst_dir, 0)
        local f = io.open(dst, "wb")
        if not f then return false, "Effects フォルダに書けません: " .. dst_dir end
        f:write(src)
        f:close()
    end
    -- 前の版の JSFX（v5 まで）が使っていたミュート・ソロの場所（8〜40）を 0 にする。
    -- 古い試験用のスクリプトの書き込みが残っていると、チャンネルのノートが止まったままになるため
    r.gmem_attach("A2M_LIVE")
    -- 9 は JSFX が始まった印なので消さない（消すと、音源の中が分からなくなったと見て送り直してしまう）
    for i = 8, 40 do if i ~= 9 then r.gmem_write(i, 0) end end
    local idx = jsfx_index(xl_track)
    if idx and changed then
        r.TrackFX_Delete(xl_track, idx)  -- 古い版を入れ直す
        idx = nil
    end
    if not idx then
        idx = r.TrackFX_AddByName(xl_track, "A2M/" .. M.JS_FILE, false, -1000)
        if idx < 0 then return false, "JSFX を X-Laben トラックに入れられませんでした" end
    elseif idx ~= 0 then
        r.TrackFX_CopyToTrack(xl_track, idx, xl_track, 0, true)
    end
    return true
end

-- ------------------------------------------------------------------ トラックの MIDI を読む
-- トラックの MIDI アイテムを、位置の順に {item, take, pos, len} で
function M.midi_items(tr)
    local list = {}
    for i = 0, r.CountTrackMediaItems(tr) - 1 do
        local it = r.GetTrackMediaItem(tr, i)
        local take = r.GetActiveTake(it)
        if take and r.TakeIsMIDI(take) and r.GetMediaItemInfo_Value(it, "B_MUTE") == 0 then
            list[#list + 1] = {item = it, take = take, pos = r.GetMediaItemInfo_Value(it, "D_POSITION"),
                               len = r.GetMediaItemInfo_Value(it, "D_LENGTH")}
        end
    end
    table.sort(list, function(a, b) return a.pos < b.pos end)
    return list
end

-- テイクのノート以外の出来事（{ppq, msg} の並び）。読んだものを覚えておき、中身（MIDI_GetHash）が変わったときだけ読み直す
-- （シークのたびに全部の MIDI を読み直すと遅いため）。M.reads は読み直した数（調べる用）
local take_cache = {}
M.reads = 0
-- 覚えている MIDI を全部捨てる（読み直すとき）
function M.clear_cache() take_cache = {} end
local function take_events(take)
    local ok_h, h = r.MIDI_GetHash(take, false, "")
    local c = take_cache[take]
    if ok_h and c and c.hash == h then return c.evs end
    local ok, buf = r.MIDI_GetAllEvts(take, "")
    local evs = (ok and buf) and chase.read_allevts(buf, -math.huge, math.huge) or {}
    M.reads = M.reads + 1
    if ok_h then take_cache[take] = {hash = h, evs = evs} end
    return evs
end

-- ------------------------------------------------------------------ ドラムのパート
-- GS の「Use for Rhythm Part」（SysEx のアドレス 40 1x 15。x：ch10 → 0、ch1〜9 → 1〜9、ch11〜16 → A〜F）。
-- 値 0 = ふつうの音色、1・2 = ドラム（ドラムマップ 1・2）。リセットのあとは ch10 だけがドラム。
-- 納品データで確かめた：サブドラムのある曲（VD540068）は 40 19 15 02（ch9 をドラムマップ 2）、無い曲（VD540525）には無い
M.RHYTHM_BLOCK = {[10] = 0x10}
for ch = 1, 9 do M.RHYTHM_BLOCK[ch] = 0x10 + ch end
for ch = 11, 16 do M.RHYTHM_BLOCK[ch] = 0x10 + ch - 1 end

-- SysEx のメッセージの並び（鳴る順）から、ドラムのパートの集合 {[ch] = true}（最後の状態）
function M.rhythm_from_sysex(msgs)
    local ch_of = {}
    for ch, b in pairs(M.RHYTHM_BLOCK) do ch_of[b] = ch end
    local drum = {[10] = true}
    for _, m in ipairs(msgs) do
        if sysex.is_reset(m) then
            drum = {[10] = true}
        else
            local p = sysex.parse(m)
            if p and p.a1 == 0x40 and p.a3 == 0x15 and ch_of[p.a2] and p.data[1] then
                drum[ch_of[p.a2]] = p.data[1] > 0 or nil
            end
        end
    end
    return drum
end

-- プロジェクトのドラムのパート（SysEx のトラック「A01 SYSTEM_EXCLUSIVE」を全部見る。無ければ ch10 だけ）
function M.rhythm_parts(tracks)
    if not tracks.sysex then return {[10] = true} end
    return M.rhythm_from_sysex(M.messages_until(tracks.sysex, math.huge, "sysex"))
end

-- トラックの最初のノートオンの時刻（秒、位置 0 以上）。ノートが無ければ math.huge。
-- 「曲の頭の値」は、これより前（セットアップ）にあるもの
function M.first_note_time(tr)
    local best = math.huge
    for _, e in ipairs(M.midi_items(tr)) do
        if e.pos < best then
            local ok, buf = r.MIDI_GetAllEvts(e.take, "")
            if ok and buf then
                local pos, ppq, n = 1, 0, #buf
                while pos <= n - 8 do
                    local offset, flags, msg
                    offset, flags, msg, pos = string.unpack("<i4Bs4", buf, pos)
                    ppq = ppq + offset
                    local st, _, vel = msg:byte(1, 3)
                    if st and st < 0xF0 and (st & 0xF0) == 0x90 and (vel or 0) > 0 and (flags & 2) == 0 then
                        local t = r.MIDI_GetProjTimeFromPPQPos(e.take, ppq)
                        if t >= -1e-9 and t >= e.pos - 1e-9 then
                            if t < best then best = t end
                            break
                        end
                    end
                end
            end
        end
    end
    return best
end

-- トラックの、t_limit 秒まで（その位置も含む）に鳴るノート以外のメッセージ（文字列）を、鳴る順に。
-- want = "short"（CC・プログラムなど）か "sysex"
function M.messages_until(tr, t_limit, want)
    local out = {}
    for _, e in ipairs(M.midi_items(tr)) do
        if e.pos <= t_limit + 1e-9 then
            local lim = math.min(t_limit, e.pos + e.len)
            local from_ppq = r.MIDI_GetPPQPosFromProjTime(e.take, e.pos) - 0.5
            local to_ppq = r.MIDI_GetPPQPosFromProjTime(e.take, lim) + 0.5
            for _, ev in ipairs(take_events(e.take)) do
                if ev[1] > to_ppq then break end
                if ev[1] >= from_ppq then
                    local is_sysex = ev[2]:byte(1) == 0xF0
                    if (want == "sysex") == is_sysex then out[#out + 1] = ev[2] end
                end
            end
        end
    end
    return out
end

-- 位置 t（秒）から再生するときに、先に音源へ送るメッセージ（SysEx が先、そのあとチャンネルごと）
function M.chase_messages(tracks, t)
    local out = {}
    for _, e in ipairs(M.chase_schedule(tracks, t)) do out[#out + 1] = e[1] end
    return out
end

M.STEP_MS = 1          -- メッセージの間（前の FastPreroll の 1 tick と同じくらい）
M.RESET_WAIT_MS = 50   -- 音源全体のリセットのあとに待つ時間（GS の決まり）

-- 送る順番と間：{{msg, 前のメッセージから待つミリ秒}, ...}
-- SysEx を1つずつ STEP_MS あけて送り（リセットのあとは RESET_WAIT_MS）、そのあとチャンネルのメッセージを、
-- 各チャンネルの n 番目どうしを同時に、n ごとに STEP_MS あけて送る（前の FastPreroll と同じ並べ方）
function M.chase_schedule(tracks, t)
    local out = {}
    local after_reset = false
    local function add(msg, delay)
        if #out == 0 then delay = 0 elseif after_reset then delay = math.max(delay, M.RESET_WAIT_MS) end
        out[#out + 1] = {msg, delay}
        after_reset = msg:byte(1) == 0xF0 and sysex.is_reset(msg)
    end
    if tracks.sysex then
        for _, m in ipairs(chase.chase_sysex(M.messages_until(tracks.sysex, t, "sysex"))) do add(m, M.STEP_MS) end
    end
    local per = {}
    for ch = 1, 16 do
        local tr = tracks.ch[ch]
        if tr then per[#per + 1] = chase.chase_channel(M.messages_until(tr, t, "short"), ch) end
    end
    local step = 1
    while true do
        local first = true
        for _, list in ipairs(per) do
            local m = list[step]
            if m then
                add(m, first and M.STEP_MS or 0)
                first = false
            end
        end
        if first then break end
        step = step + 1
    end
    return out
end

return M
