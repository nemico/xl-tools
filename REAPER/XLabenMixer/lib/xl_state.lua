-- ミキサーが表示する値の、時間の流れ（チャンネルごと・コントローラーごとに「いつ、いくつになったか」）。
-- 再生位置で効いている値は value_at で引く（DAW のオートメーションの「読む」と同じ）。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local chase = dofile(DIR .. "xl_chase.lua")
local sysex = dofile(DIR .. "xl_sysex.lua")

local M = {}

-- 覚えておく CC と、何もないときの値（GS の初期値）
M.CC = {[0] = 0, [32] = 0, [7] = 100, [10] = 64, [11] = 127, [91] = 40, [93] = 0, [94] = 0}

-- ------------------------------------------------------------------ 時間をたどる（REAPER を使わない）
-- list は {t, v} の時刻の順。t 以前で最後の値（無ければ default）
function M.value_at(list, t, default)
    if not list or #list == 0 or list[1][1] > t + 1e-9 then return default end
    local lo, hi = 1, #list
    while lo < hi do
        local mid = (lo + hi + 1) // 2
        if list[mid][1] <= t + 1e-9 then lo = mid else hi = mid - 1 end
    end
    return list[lo][2]
end

-- チャンネルのメッセージ {t, msg}（時刻の順）→ {cc = {[番号] = {{t, v}, ...}}, program = {{t, {msb, lsb, pc}}, ...}}
function M.build_channel(events)
    local tl = {cc = {}, program = {}}
    for n in pairs(M.CC) do tl.cc[n] = {} end
    local msb, lsb = 0, 0
    for _, e in ipairs(events) do
        local st, d1, d2 = e[2]:byte(1, 3)
        local kind = st & 0xF0
        if kind == 0xB0 and tl.cc[d1] then
            local l = tl.cc[d1]
            l[#l + 1] = {e[1], d2}
            if d1 == 0 then msb = d2 elseif d1 == 32 then lsb = d2 end
        elseif kind == 0xC0 then
            tl.program[#tl.program + 1] = {e[1], {msb, lsb, d1}}
        end
    end
    return tl
end

-- SysEx のトラックのメッセージ {t, msg} → {efx = {[ch] = {{t, v}, ...}}}
function M.build_sysex(events)
    local tl = {efx = {}}
    for ch = 1, 16 do tl.efx[ch] = {} end
    for _, e in ipairs(events) do
        local ch, v = sysex.efx_assign(e[2])
        if ch then
            local l = tl.efx[ch]
            l[#l + 1] = {e[1], v}
        end
    end
    return tl
end

-- ------------------------------------------------------------------ プロジェクトから読む
-- トラックの、ノート以外のメッセージを {t, msg} で（時刻の順）。want = "short" / "sysex"
function M.read_track(proj, tr, want)
    local out = {}
    for _, e in ipairs(proj.midi_items(tr)) do
        local from_ppq = r.MIDI_GetPPQPosFromProjTime(e.take, e.pos)
        local to_ppq = r.MIDI_GetPPQPosFromProjTime(e.take, e.pos + e.len)
        local ok, buf = r.MIDI_GetAllEvts(e.take, "")
        if ok and buf then
            for _, ev in ipairs(chase.read_allevts(buf, from_ppq - 0.5, to_ppq - 0.5)) do
                local msg = ev[2]
                local is_sysex = msg:byte(1) == 0xF0
                local keep
                if want == "sysex" then
                    keep = is_sysex and sysex.efx_assign(msg) ~= nil
                else
                    local kind = msg:byte(1) & 0xF0
                    keep = not is_sysex and ((kind == 0xB0 and M.CC[msg:byte(2)] ~= nil) or kind == 0xC0)
                end
                if keep then out[#out + 1] = {r.MIDI_GetProjTimeFromPPQPos(e.take, ev[1]), msg} end
            end
        end
    end
    table.sort(out, function(a, b) return a[1] < b[1] end)
    return out
end

-- 変わったかを見るための印（アイテムの位置・長さ・中身、テンポ）
local function track_signature(proj, tr)
    local parts = {}
    for _, e in ipairs(proj.midi_items(tr)) do
        local _, hash = r.MIDI_GetHash(e.take, false, "")
        parts[#parts + 1] = string.format("%s@%.6f+%.6f:%s", tostring(e.take), e.pos, e.len, hash)
    end
    return table.concat(parts, "|")
end

local function tempo_signature()
    local parts = {}
    for i = 0, r.CountTempoTimeSigMarkers(0) - 1 do
        local _, t, _, _, bpm, num, den = r.GetTempoTimeSigMarker(0, i)
        parts[#parts + 1] = string.format("%.6f/%.4f/%d/%d", t, bpm, num, den)
    end
    return table.concat(parts, "|")
end

-- 覚えておく入れ物。refresh で、変わったトラックだけ読み直す
function M.new_cache()
    return {sig = {}, ch = {}, sysex = {efx = {}}, tempo = nil, state_count = -1}
end

function M.refresh(cache, proj, tracks, force)
    local count = r.GetProjectStateChangeCount(0)
    if not force and count == cache.state_count then return false end
    cache.state_count = count
    local tempo = tempo_signature()
    local tempo_changed = tempo ~= cache.tempo
    cache.tempo = tempo
    local changed = false
    for ch = 1, 16 do
        local tr = tracks.ch[ch]
        if tr then
            local sig = track_signature(proj, tr)
            if force or tempo_changed or sig ~= cache.sig["ch" .. ch] then
                cache.sig["ch" .. ch] = sig
                cache.ch[ch] = M.build_channel(M.read_track(proj, tr, "short"))
                cache.ch[ch].first_note = proj.first_note_time and proj.first_note_time(tr) or math.huge
                changed = true
            end
        else
            cache.ch[ch] = nil
        end
    end
    if tracks.sysex then
        local sig = track_signature(proj, tracks.sysex)
        if force or tempo_changed or sig ~= cache.sig.sysex then
            cache.sig.sysex = sig
            cache.sysex = M.build_sysex(M.read_track(proj, tracks.sysex, "sysex"))
            changed = true
        end
    end
    return changed
end

-- 曲の頭（セットアップ）の値：時刻 0 以降、最初のノートより前で最初のもの。無ければ default
-- （曲の途中にしか無いものは、曲の頭の値ではない）
function M.initial(cache, ch, num, default)
    local tl = cache.ch[ch]
    local list = tl and tl.cc[num]
    local limit = tl and tl.first_note or math.huge
    if list then
        for _, e in ipairs(list) do
            if e[1] >= -1e-9 then
                if e[1] < limit - 1e-9 then return e[2] end
                break
            end
        end
    end
    return default
end

-- t（秒）で効いている、チャンネルの値の表
function M.values(cache, ch, t)
    local tl = cache.ch[ch]
    local v = {}
    for n, d in pairs(M.CC) do v[n] = tl and M.value_at(tl.cc[n], t, d) or d end
    v.program = tl and M.value_at(tl.program, t, nil) or nil
    v.efx = M.value_at(cache.sysex.efx and cache.sysex.efx[ch], t, 0)
    return v
end

return M
