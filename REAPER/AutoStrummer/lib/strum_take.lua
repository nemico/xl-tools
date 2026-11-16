-- MIDI のテイクを読む・書く（REAPER）。ノートは番号ではなく、読んだときの中身で見分ける。
local r = reaper
local M = {}

-- 対象のテイク：MIDI エディターで開いているもの、無ければ選んでいるアイテムのもの
function M.active_take()
    local me = r.MIDIEditor_GetActive()
    if me then
        local take = r.MIDIEditor_GetTake(me)
        if take and r.TakeIsMIDI(take) then return take end
    end
    local item = r.GetSelectedMediaItem(0, 0)
    local take = item and r.GetActiveTake(item)
    if take and r.TakeIsMIDI(take) then return take end
    return nil
end

-- 選んでいるノート：{idx, muted, chan, pitch, vel, start, stop（秒）, sppq, eppq}
function M.selected_notes(take)
    local out = {}
    if not take then return out end
    local idx = r.MIDI_EnumSelNotes(take, -1)
    while idx and idx >= 0 do
        local ok, sel, muted, sppq, eppq, chan, pitch, vel = r.MIDI_GetNote(take, idx)
        if ok and sel then
            out[#out + 1] = {idx = idx, muted = muted, chan = chan, pitch = pitch, vel = vel, sppq = sppq, eppq = eppq,
                             start = r.MIDI_GetProjTimeFromPPQPos(take, sppq), stop = r.MIDI_GetProjTimeFromPPQPos(take, eppq)}
        end
        idx = r.MIDI_EnumSelNotes(take, idx)
    end
    return out
end

-- 選んでいるノートの印（どれかが変われば変わる：選び直した・動かした・元に戻した）
function M.signature(take, notes)
    if not take then return "" end
    local parts = {tostring(take)}
    for _, n in ipairs(notes) do
        parts[#parts + 1] = string.format("%d:%d:%d:%d:%.0f:%.0f", n.idx, n.chan, n.pitch, n.vel, n.sppq, n.eppq)
    end
    return table.concat(parts, "|")
end

-- 並べ方（チャンネル・高さ・始まり）：もとのノートと今のノートを 1 対 1 で結ぶため
local function by_key(list)
    local o = {}
    for i, n in ipairs(list) do o[i] = n end
    table.sort(o, function(a, b)
        if a.chan ~= b.chan then return a.chan < b.chan end
        if a.pitch ~= b.pitch then return a.pitch < b.pitch end
        return a.start < b.start
    end)
    return o
end

-- もとのノート（base）から計算した位置（plan）を、今選んでいるノート（now）に書く。Ctrl+Z で 1 回で戻る。
-- base と now は同じノート（数とチャンネル・高さが同じ）であること。書いたら true
function M.apply(take, base, now, plan, label)
    if #base == 0 or #base ~= #now then return false end
    local b, n = by_key(base), by_key(now)
    for i = 1, #b do
        if b[i].chan ~= n[i].chan or b[i].pitch ~= n[i].pitch then return false end
    end
    r.Undo_BeginBlock()
    r.PreventUIRefresh(1)
    for i = 1, #b do
        local p = plan[b[i]]
        if p then
            local x = n[i]
            local sppq = math.floor(r.MIDI_GetPPQPosFromProjTime(take, p.start) + 0.5)
            local eppq = math.floor(r.MIDI_GetPPQPosFromProjTime(take, p.stop) + 0.5)
            if eppq <= sppq then eppq = sppq + 1 end   -- 長さ 0 にはしない
            r.MIDI_SetNote(take, x.idx, true, x.muted, sppq, eppq, x.chan, x.pitch, p.vel, true)
        end
    end
    r.MIDI_Sort(take)
    r.PreventUIRefresh(-1)
    r.Undo_EndBlock(label or "ストラム", -1)
    return true
end

-- 編集カーソルの位置で鳴っているノートだけを選ぶ（Ruler Select）
function M.select_at(take, t)
    if not take then return end
    local ppq = r.MIDI_GetPPQPosFromProjTime(take, t)
    local _, count = r.MIDI_CountEvts(take)
    r.PreventUIRefresh(1)
    for i = 0, count - 1 do
        local ok, sel, muted, sppq, eppq, chan, pitch, vel = r.MIDI_GetNote(take, i)
        if ok then
            local on = ppq >= sppq and ppq < eppq
            if sel ~= on then r.MIDI_SetNote(take, i, on, muted, sppq, eppq, chan, pitch, vel, true) end
        end
    end
    r.PreventUIRefresh(-1)
end

return M
