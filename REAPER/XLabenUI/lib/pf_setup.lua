-- トラックのセットアップ（最初のノートより前）の音色：バンク（CC0 / CC32）とプログラムチェンジを読む・書く。
-- 効いているのは、最初のノートより前の最後のプログラムチェンジと、その時点までの最後の CC0 / CC32（GS の決まり：
-- バンクはプログラムチェンジを受けたときに決まる）。書くときはその 3 つを書き換える（位置は動かさない。足さない）。
local r = reaper
local M = {}

-- t（秒）の順に {take, idx, t, ppq, kind = "pc" / 0 / 32, value, chan}（位置 0 以上、limit 秒より前）
local function setup_events(proj, tr, limit)
    local out = {}
    for _, e in ipairs(proj.midi_items(tr)) do
        local _, _, n_cc = r.MIDI_CountEvts(e.take)
        for i = 0, n_cc - 1 do
            local ok, _, _, ppq, chanmsg, chan, d1, d2 = r.MIDI_GetCC(e.take, i)
            if ok then
                local kind, value
                if chanmsg == 0xC0 then kind, value = "pc", d1
                elseif chanmsg == 0xB0 and (d1 == 0 or d1 == 32) then kind, value = d1, d2 end
                if kind then
                    local t = r.MIDI_GetProjTimeFromPPQPos(e.take, ppq)
                    if t >= -1e-9 and t < limit - 1e-9 then
                        out[#out + 1] = {take = e.take, idx = i, t = t, ppq = ppq, kind = kind, value = value, chan = chan}
                    end
                end
            end
        end
    end
    table.sort(out, function(a, b) if a.t ~= b.t then return a.t < b.t end return a.idx < b.idx end)
    return out
end

-- {msb, lsb, pc, ev = {pc, [0], [32]}}。最初のノートより前にプログラムチェンジが無ければ nil
function M.read(proj, tr)
    if not tr then return nil end
    local evs = setup_events(proj, tr, proj.first_note_time(tr))
    local pc
    for _, e in ipairs(evs) do if e.kind == "pc" then pc = e end end
    if not pc then return nil end
    local bank = {}
    for _, e in ipairs(evs) do
        if e.kind ~= "pc" and e.t <= pc.t + 1e-9 and (e.take ~= pc.take or e.t < pc.t or e.idx < pc.idx) then bank[e.kind] = e end
    end
    return {msb = bank[0] and bank[0].value, lsb = bank[32] and bank[32].value, pc = pc.value,
            ev = {pc = pc, [0] = bank[0], [32] = bank[32]}}
end

-- 音色を書く（3 つとも、最初のノートより前にあるときだけ）。(true) か (false, 理由)。元に戻す（Ctrl+Z）で 1 回で戻る
function M.write(proj, tr, tone, label)
    local cur = M.read(proj, tr)
    if not cur then return false, "セットアップ（最初のノートより前）にプログラムチェンジがありません（足しません）" end
    local missing = {}
    if not cur.ev[0] then missing[#missing + 1] = "CC0（バンク MSB）" end
    if not cur.ev[32] then missing[#missing + 1] = "CC32（バンク LSB）" end
    if #missing > 0 then
        return false, "セットアップに " .. table.concat(missing, "・") .. " がありません（足しません）"
    end
    r.Undo_BeginBlock()
    local takes = {}
    local function put(e, d1, d2)
        local ok, sel, mute, ppq, chanmsg, chan = r.MIDI_GetCC(e.take, e.idx)
        if ok then r.MIDI_SetCC(e.take, e.idx, sel, mute, ppq, chanmsg, chan, d1, d2, true) end
        takes[e.take] = true
    end
    put(cur.ev[0], 0, tone.msb)
    put(cur.ev[32], 32, tone.lsb)
    put(cur.ev.pc, tone.pc, 0)
    for take in pairs(takes) do
        r.MIDI_Sort(take)
        r.UpdateItemInProject(r.GetMediaItemTake_Item(take))
    end
    r.Undo_EndBlock(label or "音色を変える", -1)
    r.UpdateArrange()
    return true
end

-- t（秒）の位置で効いている音色 {msb, lsb, pc}（そこまでの最後のプログラムチェンジと、その時点のバンク）。PC が無ければ nil
function M.tone_at(proj, tr, t)
    local bank, out = {}, nil
    for _, m in ipairs(proj.messages_until(tr, t, "short")) do
        local st, d1, d2 = m:byte(1, 3)
        if st & 0xF0 == 0xB0 and (d1 == 0 or d1 == 32) then bank[d1] = d2
        elseif st & 0xF0 == 0xC0 then out = {msb = bank[0], lsb = bank[32], pc = d1} end
    end
    return out
end

-- 音源へ送るメッセージ（バンク → プログラム）
function M.messages(ch, msb, lsb, pc)
    local s = 0xB0 | (ch - 1)
    return {string.char(s, 0, msb or 0), string.char(s, 32, lsb or 0), string.char(0xC0 | (ch - 1), pc)}
end

return M
