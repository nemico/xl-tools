-- ドラムのトラックを読む・書く（REAPER を使う部分）。
-- トラックは、上から最初に見つかった「A10R…」（ch10）・「A09R…」（ch9）（XLaben_Drum.lua で決める）。
-- 書くのは、そのトラックの最初のノートのときに効いている NRPN のブロック（曲の頭の値）だけ。
-- サブドラム（ch9）のように、曲の途中から始まり、セットアップのアイテムが曲の途中に2つ（プログラムチェンジが2回）あることがある。
-- 会社のマクロ「NRPN使用キット入力」は、プログラムチェンジごとの区間に分けて、音のある区間のブロックに値を入れる
-- （音の無い区間には中身の無い見出しだけが残る）。なので「一番早いアイテム」ではなく「最初のノートの直前のブロック」を使う。
local r = reaper
local DIR = debug.getinfo(1, "S").source:match("^@?(.*[\\/])")
local block = dofile(DIR .. "drum_block.lua")

local M = {}

-- 曲の頭のセットアップのアイテム（位置 0 以上で一番早いもの。頭より前に置いた退避のアイテムは使わない）
function M.setup_item(tr)
    local best, best_pos = nil, math.huge
    for i = 0, r.CountTrackMediaItems(tr) - 1 do
        local it = r.GetTrackMediaItem(tr, i)
        local pos = r.GetMediaItemInfo_Value(it, "D_POSITION")
        local take = r.GetActiveTake(it)
        if pos >= -1e-9 and pos < best_pos and take and r.TakeIsMIDI(take) then best, best_pos = it, pos end
    end
    return best
end

-- テイクの、納品の 10 tick（480 分解能）にあたる ppq
function M.step_of(take)
    local q0 = r.MIDI_GetProjQNFromPPQPos(take, 0)
    local per_qn = r.MIDI_GetPPQPosFromProjQN(take, q0 + 1) - r.MIDI_GetPPQPosFromProjQN(take, q0)
    if not per_qn or per_qn <= 0 then per_qn = block.DELIVERY_TPQ end
    return block.DELIVERY_STEP * per_qn / block.DELIVERY_TPQ, block.START_GAP * per_qn / block.DELIVERY_TPQ
end

local function events_of(take)
    local ok, buf = r.MIDI_GetAllEvts(take, "")
    if not ok or not buf then return {} end
    return block.decode(buf)
end

-- 曲の頭のキット：セットアップの、最初のノートより前の最後のバンク（CC0 / CC32）とプログラム
local function kit_of(events, ch0)
    local msb, lsb, pc = nil, nil, nil
    for _, e in ipairs(events) do
        local st, d1, d2 = e[3]:byte(1, 3)
        if st and (st & 0xF0) == 0x90 and (d2 or 0) > 0 then break end
        if st == (0xB0 | ch0) and d1 == 0 then msb = d2
        elseif st == (0xB0 | ch0) and d1 == 32 then lsb = d2
        elseif st == (0xC0 | ch0) then pc = d1 end
    end
    return {msb = msb, lsb = lsb, pc = pc}
end

-- {tr, ch, item, take, events, block, values（曲の頭）, kit, step, gap}。MIDI のアイテムが無ければ nil, 理由
-- 使うブロック：トラックの最初のノート（位置 0 以上、ミュートでない）より前にあるブロックのうち、一番遅いもの。
-- それが無ければ一番早いブロック、それも無ければ一番早いアイテム（新しく作るとき）
function M.read(tr, ch)
    local ch0 = ch - 1
    local items = {}
    for i = 0, r.CountTrackMediaItems(tr) - 1 do
        local it = r.GetTrackMediaItem(tr, i)
        local pos = r.GetMediaItemInfo_Value(it, "D_POSITION")
        local take = r.GetActiveTake(it)
        if pos >= -1e-9 and take and r.TakeIsMIDI(take) and r.GetMediaItemInfo_Value(it, "B_MUTE") == 0 then
            items[#items + 1] = {item = it, take = take, pos = pos}
        end
    end
    if #items == 0 then return nil, "MIDI のアイテム（位置 0 以上）がありません" end
    table.sort(items, function(a, b) return a.pos < b.pos end)
    -- トラックの最初のノートの時刻と、アイテムごとのブロック
    local first_note = math.huge
    for _, x in ipairs(items) do
        x.events = events_of(x.take)
        x.step, x.gap = M.step_of(x.take)
        for _, e in ipairs(x.events) do
            local st, _, vel = e[3]:byte(1, 3)
            if st and st < 0xF0 and (st & 0xF0) == 0x90 and (vel or 0) > 0 and (e[2] & 2) == 0 then
                local t = r.MIDI_GetProjTimeFromPPQPos(x.take, e[1])
                if t >= x.pos - 1e-6 and t < first_note then first_note = t end
                break
            end
        end
        x.block = block.find(x.events, ch0, x.step)
        if x.block then x.block_t = r.MIDI_GetProjTimeFromPPQPos(x.take, x.block.start_ppq) end
    end
    local use, earliest = nil, nil
    for _, x in ipairs(items) do
        if x.block then
            if not earliest then earliest = x end
            if x.block_t <= first_note + 1e-6 and (not use or x.block_t >= use.block_t) then use = x end
        end
    end
    use = use or earliest or items[1]
    return {tr = tr, ch = ch, item = use.item, take = use.take, events = use.events, block = use.block,
            values = block.values(use.block), kit = kit_of(use.events, ch0), step = use.step, gap = use.gap}
end

-- (msb, note) の曲の頭の値を value にする。元に戻す（Ctrl+Z）で1回で戻る。できたら true、できなければ nil, 理由
function M.write(info, msb, note, value)
    -- もうある値を変えるだけなら、その CC6 の値だけを変える（ブロックの並びも位置も動かさない。詰めたあとのデータでも書ける）
    local p = block.PARAMS[msb]
    local removing = p and p.default ~= nil and value == p.default
    local out, err = nil, nil
    if not removing then out = block.change_in_place(info.events, info.block, msb, note, value) end
    if not out then
        local entries = block.set(info.block and info.block.entries or {}, msb, note, value)
        out, err = block.rewrite(info.events, info.block, entries, info.ch - 1, info.step, info.gap)
    end
    if not out then return nil, err end
    r.Undo_BeginBlock()
    r.MIDI_SetAllEvts(info.take, block.encode(out))
    r.MIDI_Sort(info.take)
    r.UpdateItemInProject(info.item)
    r.Undo_EndBlock(string.format("ドラム ch%d ノート %d の %s を %d に", info.ch, note, block.PARAMS[msb].name, value), -1)
    return true
end

-- t（秒）の位置で効いているドラムの値（曲の途中の NRPN も入れる）
function M.values_at(proj, tr, ch, t)
    return block.values_from_messages(proj.messages_until(tr, t, "short"), ch - 1)
end

-- 音ごとに、鳴る時刻（秒、昇順）。ミュートしたアイテム・ノートは入れない。ループしているアイテムは、終わりまで繰り返す
function M.note_times(tr)
    local out = {}
    local function add(pitch, t)
        out[pitch] = out[pitch] or {}
        out[pitch][#out[pitch] + 1] = t
    end
    for i = 0, r.CountTrackMediaItems(tr) - 1 do
        local it = r.GetTrackMediaItem(tr, i)
        local take = r.GetActiveTake(it)
        local pos = r.GetMediaItemInfo_Value(it, "D_POSITION")
        local fin = pos + r.GetMediaItemInfo_Value(it, "D_LENGTH")
        if take and r.TakeIsMIDI(take) and r.GetMediaItemInfo_Value(it, "B_MUTE") == 0 and fin > 0 then
            local notes = {}
            for _, e in ipairs(events_of(take)) do
                local st, d1, d2 = e[3]:byte(1, 3)
                if st and st < 0xF0 and (st & 0xF0) == 0x90 and (d2 or 0) > 0 and (e[2] & 2) == 0 then
                    notes[#notes + 1] = {e[1], d1}
                end
            end
            local loop_len = 0
            if r.GetMediaItemInfo_Value(it, "B_LOOPSRC") == 1 and r.GetMediaItemTake_Source then
                local len, is_qn = r.GetMediaSourceLength(r.GetMediaItemTake_Source(take))
                if is_qn and len and len > 0 then loop_len = len end
            end
            for _, n in ipairs(notes) do
                if loop_len > 0 then
                    -- ループ：テイクの頭からの QN で繰り返す（テンポの変化にも合う）
                    local q0 = r.MIDI_GetProjQNFromPPQPos(take, 0)
                    local qn = r.MIDI_GetProjQNFromPPQPos(take, n[1]) - q0
                    local q_start, q_end = r.TimeMap2_timeToQN(0, pos), r.TimeMap2_timeToQN(0, fin)
                    local c = 0
                    while true do
                        local q = q0 + qn + c * loop_len
                        if q >= q_end - 1e-6 then break end
                        if q >= q_start - 1e-6 then
                            local t = r.TimeMap2_QNToTime(0, q)
                            if t >= 0 then add(n[2], t) end
                        end
                        c = c + 1
                        if c > 10000 then break end
                    end
                else
                    local t = r.MIDI_GetProjTimeFromPPQPos(take, n[1])
                    if t >= pos - 1e-3 and t <= fin + 1e-3 and t >= 0 then add(n[2], t) end
                end
            end
        end
    end
    for p, list in pairs(out) do
        table.sort(list)
        local uniq, last = {}, -math.huge
        for _, t in ipairs(list) do
            if t - last > 0.005 then uniq[#uniq + 1] = t; last = t end
        end
        out[p] = uniq
    end
    return out
end

-- 光らせる強さ（0〜1）：鳴る 60 ms 前から立ち上がり、80 ms 光ったまま、200 ms で消える（前の DrumUI と同じ）
function M.glow(times, t)
    if not times or #times == 0 then return 0 end
    local pre, hold, fade = 0.06, 0.08, 0.20
    local lo, hi, best = 1, #times, 0
    while lo <= hi do
        local mid = (lo + hi) // 2
        if times[mid] <= t + pre then best = mid; lo = mid + 1 else hi = mid - 1 end
    end
    local out = 0
    for k = best, 1, -1 do
        local d = t - times[k]
        if d > hold + fade then break end
        local v
        if d < 0 then v = 1 + d / pre * 0.4 elseif d <= hold then v = 1 else v = 1 - (d - hold) / fade end
        if v > out then out = v end
    end
    return out
end

return M
