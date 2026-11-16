-- ミキサーの値をプロジェクトに書く（CC と、EFX の割り当ての SysEx）。どれも元に戻す（Ctrl+Z）で1回で戻る。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local proj = dofile(DIR .. "xl_project.lua")
local sysex = dofile(DIR .. "xl_sysex.lua")

local M = {}
M.SYSEX_STEP = 10   -- SysEx を同じ tick に置かない（R02 の決まり）。空くまでこの tick ずつずらす

-- t（秒）にかかるアイテム（後から始まるものを優先）
local function item_at(tr, t)
    local best
    for _, e in ipairs(proj.midi_items(tr)) do
        if e.pos <= t + 1e-9 and t < e.pos + e.len - 1e-9 then best = e end
    end
    return best
end

-- 曲の頭のセットアップのアイテム（位置 0 以上で一番早いもの）
local function setup_item(tr)
    for _, e in ipairs(proj.midi_items(tr)) do
        if e.pos >= 0 then return e end
    end
end

-- 取り出しの CC を、全部のアイテムから {e, idx, t, ppq, value} で（時刻の順）
local function cc_events(tr, ch, num)
    local out = {}
    for _, e in ipairs(proj.midi_items(tr)) do
        local _, _, n_cc = r.MIDI_CountEvts(e.take)
        for i = 0, n_cc - 1 do
            local ok, _, _, ppq, chanmsg, chan, d1, d2 = r.MIDI_GetCC(e.take, i)
            if ok and chanmsg == 0xB0 and d1 == num then
                out[#out + 1] = {e = e, idx = i, ppq = ppq, value = d2, chan = chan,
                                 t = r.MIDI_GetProjTimeFromPPQPos(e.take, ppq)}
            end
        end
    end
    table.sort(out, function(a, b) return a.t < b.t end)
    return out
end

local function set_cc(ev, value)
    local ok, sel, mute, ppq, chanmsg, chan, d1 = r.MIDI_GetCC(ev.e.take, ev.idx)
    if ok then r.MIDI_SetCC(ev.e.take, ev.idx, sel, mute, ppq, chanmsg, chan, d1, value, true) end
end

local function finish(takes, label)
    for take in pairs(takes) do
        r.MIDI_Sort(take)
        r.UpdateItemInProject(r.GetMediaItemTake_Item(take))
    end
    r.Undo_EndBlock(label, -1)
    r.UpdateArrange()
end

-- 曲の頭の値（最初のノートより前の、一番早い CC）を変える。無ければセットアップのアイテムの、最初の CC と同じ位置に足す
-- （曲の途中の CC は変えない）
function M.cc_initial(tr, ch, num, value)
    r.Undo_BeginBlock()
    local takes = {}
    local first
    local limit = proj.first_note_time(tr)
    for _, ev in ipairs(cc_events(tr, ch, num)) do
        if ev.t >= -1e-9 then
            if ev.t < limit - 1e-9 then first = ev end
            break
        end
    end
    if first then
        set_cc(first, value)
        takes[first.e.take] = true
    else
        local e = setup_item(tr)
        if not e then
            r.Undo_EndBlock("（何もしない）", -1)
            return false, "セットアップの MIDI アイテムがありません"
        end
        local _, _, n_cc = r.MIDI_CountEvts(e.take)
        local ppq = 0
        if n_cc > 0 then
            local _, _, _, p = r.MIDI_GetCC(e.take, 0)
            ppq = p
        end
        r.MIDI_InsertCC(e.take, false, false, ppq, 0xB0, ch - 1, num, value)
        takes[e.take] = true
    end
    finish(takes, string.format("ch%d CC%d の初期値を %d に", ch, num, value))
    return true
end

-- t（秒）に値を置く（同じ位置にあれば書き換え）。内部用：Undo は呼ぶ側
local function put_cc(tr, ch, num, value, t, takes)
    local e = item_at(tr, t)
    if not e then return false, "その位置に MIDI アイテムがありません" end
    local ppq = math.floor(r.MIDI_GetPPQPosFromProjTime(e.take, t) + 0.5)
    local _, _, n_cc = r.MIDI_CountEvts(e.take)
    for i = 0, n_cc - 1 do
        local ok, _, _, p, chanmsg, _, d1 = r.MIDI_GetCC(e.take, i)
        if ok and chanmsg == 0xB0 and d1 == num and math.abs(p - ppq) < 0.5 then
            set_cc({e = e, idx = i}, value)
            takes[e.take] = true
            return true
        end
    end
    r.MIDI_InsertCC(e.take, false, false, ppq, 0xB0, ch - 1, num, value)
    takes[e.take] = true
    return true
end

-- t1 < 時刻 < t2 の、その CC を消す
local function delete_between(tr, ch, num, t1, t2, takes)
    local list = cc_events(tr, ch, num)
    -- 後ろから消す（同じテイクの番号がずれないように）
    for i = #list, 1, -1 do
        local ev = list[i]
        if ev.t > t1 + 1e-6 and ev.t < t2 - 1e-6 then
            r.MIDI_DeleteCC(ev.e.take, ev.idx)
            takes[ev.e.take] = true
        end
    end
end

-- t（秒）から値を変える。範囲（t2 あり）なら、その間の同じ CC を消し、t2 で元の値（v_end）に戻す
function M.cc_from(tr, ch, num, value, t, t2, v_end)
    r.Undo_BeginBlock()
    local takes = {}
    if t2 and t2 > t then delete_between(tr, ch, num, t, t2, takes) end
    local ok, err = put_cc(tr, ch, num, value, t, takes)
    if ok and t2 and t2 > t and v_end and v_end ~= value then
        ok, err = put_cc(tr, ch, num, v_end, t2, takes)
    end
    finish(takes, string.format("ch%d CC%d を %d に", ch, num, value))
    return ok, err
end

-- タッチで書いた点 {{t, v}, ...}（時刻の順）を書く。最初の点から t_end までの同じ CC を消し、t_end で v_end に戻す
function M.cc_points(tr, ch, num, points, t_end, v_end)
    if #points == 0 then return true end
    r.Undo_BeginBlock()
    local takes = {}
    local t0 = points[1][1]
    delete_between(tr, ch, num, t0 - 1e-5, t_end + 1e-5, takes)
    local ok, err = true, nil
    for _, p in ipairs(points) do
        local ok1, err1 = put_cc(tr, ch, num, p[2], p[1], takes)
        if not ok1 then ok, err = false, err1 end
    end
    if v_end and v_end ~= points[#points][2] then put_cc(tr, ch, num, v_end, t_end, takes) end
    finish(takes, string.format("ch%d CC%d をタッチで書く（%d 点）", ch, num, #points))
    return ok, err
end

-- ------------------------------------------------------------------ EFX の割り当て（SysEx のトラック）
local function strip_f0(msg)
    if msg:byte(1) == 0xF0 then msg = msg:sub(2) end
    if msg:byte(-1) == 0xF7 then msg = msg:sub(1, -2) end
    return msg
end

local function efx_events(tr, ch)
    local out = {}
    for _, e in ipairs(proj.midi_items(tr)) do
        local _, _, _, n_tx = r.MIDI_CountEvts(e.take)
        for i = 0, n_tx - 1 do
            local ok, sel, mute, ppq, typ, msg = r.MIDI_GetTextSysexEvt(e.take, i)
            if ok and typ == -1 then
                local c = sysex.efx_assign(msg)
                if c == ch then
                    out[#out + 1] = {e = e, idx = i, ppq = ppq, msg = msg, sel = sel, mute = mute,
                                     t = r.MIDI_GetProjTimeFromPPQPos(e.take, ppq)}
                end
            end
        end
    end
    table.sort(out, function(a, b) return a.t < b.t end)
    return out
end

-- 曲の頭の割り当て（一番早いもの）を変える。無ければ足さない（セットアップの並びの決まりがあるため）
function M.efx_initial(tr, ch, value)
    local first
    for _, ev in ipairs(efx_events(tr, ch)) do
        if ev.t >= -1e-9 then first = ev break end
    end
    if not first then return false, string.format("ch%d の EFX の割り当て（セットアップの SysEx）が見つかりません", ch) end
    r.Undo_BeginBlock()
    local new = sysex.with_data(first.msg, {value})
    r.MIDI_SetTextSysexEvt(first.e.take, first.idx, first.sel, first.mute, first.ppq, -1, strip_f0(new), true)
    finish({[first.e.take] = true}, string.format("ch%d の EFX の初期値を %s に", ch, ({[0] = "オフ", "A", "B"})[value]))
    return true
end

-- t（秒）から割り当てを変える。ほかの SysEx と同じ tick にならないように後ろへずらす
function M.efx_from(tr, ch, value, t)
    local e = item_at(tr, t)
    if not e then return false, "その位置に SysEx のアイテムがありません" end
    local used = {}
    local _, _, _, n_tx = r.MIDI_CountEvts(e.take)
    for i = 0, n_tx - 1 do
        local ok, _, _, ppq, typ = r.MIDI_GetTextSysexEvt(e.take, i)
        if ok and typ == -1 then used[math.floor(ppq + 0.5)] = true end
    end
    local ppq = math.floor(r.MIDI_GetPPQPosFromProjTime(e.take, t) + 0.5)
    while used[ppq] do ppq = ppq + M.SYSEX_STEP end
    r.Undo_BeginBlock()
    r.MIDI_InsertTextSysexEvt(e.take, false, false, ppq, -1, strip_f0(sysex.efx_assign_msg(ch, value)))
    finish({[e.take] = true}, string.format("ch%d の EFX を %s に", ch, ({[0] = "オフ", "A", "B"})[value]))
    return true
end

return M
