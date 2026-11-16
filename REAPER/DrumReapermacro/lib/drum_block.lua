-- ドラムの曲の頭の NRPN（音ごとのピッチ・レベル・パン・リバーブ・コーラス・ディレイ）のまとまり（ブロック）。
-- 読む・値を変える・並べ直す。REAPER を使わない部分（テストで確かめる）。
--
-- 納品の形（納品データ 10 曲で確かめた）：
--   セットアップのアイテムの頭、パートの NRPN（MSB 1）のあとに、
--   CC99 = MSB（24 → 26 → 28 → 29 → 30 → 31 の順）、そのあと CC98 = ノート番号・CC6 = 値 の組を繰り返す。
--   すべて 10 tick おき（480 分解能で。納品の決まり）。レベル・パン・リバーブは使う音すべてにある。
--   ピッチ・コーラス・ディレイは、変えた音だけ（既定 64 / 0 / 0 なら無い）。
-- 書き換えてよいのはブロックの中だけ。ブロックの外のイベントは動かさない。
local M = {}

M.MSB_ORDER = {24, 26, 28, 29, 30, 31}
M.PARAMS = {
    [24] = {name = "Pitch", default = 64, bipolar = true},
    [26] = {name = "Level"},
    [28] = {name = "Pan", bipolar = true},
    [29] = {name = "Rev"},
    [30] = {name = "Cho", default = 0},
    [31] = {name = "Del", default = 0},
}
M.DELIVERY_STEP = 10      -- 納品の決まり：ブロックの中は 10 tick おき（480 分解能）
M.DELIVERY_TPQ = 480
M.START_GAP = 20          -- ブロックが無いとき、セットアップの最後のイベントから少なくとも空ける tick
M.FROM_PC = 240           -- ブロックが無いとき、プログラムチェンジから空ける tick（会社のマクロ「カラオケ_ドラムNRPN」と同じ。納品データの 760）

local DRUM_MSB = {}
for msb = 24, 31 do DRUM_MSB[msb] = true end

local function is_note_on(msg)
    local st, _, vel = msg:byte(1, 3)
    return st and st < 0xF0 and (st & 0xF0) == 0x90 and (vel or 0) > 0
end

local function cc_of(msg, ch0)
    local st, d1, d2 = msg:byte(1, 3)
    if st == (0xB0 | ch0) and d1 then return d1, d2 end
end

-- events：{{ppq, flags, msg}, ...}（ppq は頭からの位置、並びは MIDI_GetAllEvts の順）。ch0 はチャンネル（0 始まり）。
-- step はブロックの中の間（テイクの ppq で）。
-- ブロック：{first, last（events の番号）, members（ブロックのイベントの番号の並び）, start_ppq, end_ppq,
--           entries = {{msb, note, value, idx（その CC6 の events の番号）}, ...}}。無ければ nil。
-- 最初のノートより前（同じ tick まで）の、CC99 = 24〜31 から始まる 99 / 98 / 6 の続きをブロックとする。
-- 終わりは会社のマクロ「NRPN詰め」と同じ：ほかのイベント（ノートも）が来たところ、または MSB の順（24 → 31）が戻ったところ。
-- ただし、ほかのイベントと同じ tick にまだブロックの続き（98 / 6）があれば、そのイベントは飛ばして続ける：
--   納品の詰めたあとのデータでは、ブロックの最後と最初のノートが同じ tick のことがある（納品データ 246 曲中 6 曲。VD540354 も）。
--   同じ tick の中の並びは、ファイルや REAPER によって、ノートが先のこともある。
-- 間は見ない（「NRPN使用キット入力」のあとは 20 tick おき・まとまりの間 200 tick に広がっているため）
local MSB_RANK = {[24] = 1, [25] = 2, [26] = 3, [27] = 4, [28] = 5, [29] = 6, [30] = 7, [31] = 8}

-- テキスト・メタのイベント（FF xx …）
local function is_text(msg) return msg:byte(1) == 0xFF end

local function is_block_data(e, ch0)
    local c1 = cc_of(e[3], ch0)
    return (c1 == 98 or c1 == 6) and (e[2] & 2) == 0
end

function M.find(events, ch0, step)
    local first_note = math.huge
    for _, e in ipairs(events) do
        if is_note_on(e[3]) then first_note = e[1]; break end
    end
    for i, e in ipairs(events) do
        if e[1] > first_note then return nil end
        local d1, d2 = cc_of(e[3], ch0)
        if d1 == 99 and DRUM_MSB[d2] and (e[2] & 2) == 0 then
            local entries, members, msb, note = {}, {}, nil, nil
            local labels, pending = {}, nil   -- 見出しのテキスト（{[msb] = テキストのイベントの中身}）
            local last = i
            -- 始まりの CC99 の直前（同じ tick）に見出しがあれば、それもブロックに入れる
            local first = i
            if i > 1 and is_text(events[i - 1][3]) and events[i - 1][1] == e[1] then
                first = i - 1
                members[1] = i - 1
                pending = events[i - 1][3]
            end
            local k = i
            while k <= #events do
                local x = events[k]
                local c1, c2 = cc_of(x[3], ch0)
                local ours = (c1 == 99 or c1 == 98 or c1 == 6) and (x[2] & 2) == 0
                if ours and c1 == 99 and (not DRUM_MSB[c2] or (msb and MSB_RANK[c2] < MSB_RANK[msb])) then
                    break  -- ドラムでない MSB、または順が戻った：次のまとまり
                end
                if not ours and is_text(x[3]) then
                    -- 見出しのテキスト（会社のマクロも「テキストは無視」）：ブロックの一部。次の CC99 の見出しにする
                    members[#members + 1] = k
                    pending = x[3]
                    last = k
                elseif not ours then
                    -- 同じ tick の後ろに、ブロックの続きがあるか
                    local more = false
                    for j = k + 1, #events do
                        if events[j][1] ~= x[1] then break end
                        if is_block_data(events[j], ch0) then more = true; break end
                    end
                    if not more then break end
                else
                    members[#members + 1] = k
                    if c1 == 99 then
                        msb, note = c2, nil
                        if pending then labels[msb] = pending; pending = nil end
                    elseif c1 == 98 then
                        note = c2
                    elseif msb and note then
                        entries[#entries + 1] = {msb = msb, note = note, value = c2, idx = k}
                    end
                    last = k
                end
                k = k + 1
            end
            -- 終わりに残った見出し（次の CC99 が無い）は、ブロックに入れない
            if pending and members[#members] and is_text(events[members[#members]][3]) then
                members[#members] = nil
                last = members[#members] or i
            end
            return {first = first, last = last, members = members, start_ppq = events[first][1], end_ppq = events[last][1],
                    entries = entries, labels = labels}
        end
    end
    return nil
end

-- ブロックの値の表 {[msb] = {[note] = 値}}（同じものが2つあれば後のもの）
function M.values(block)
    local out = {}
    for msb = 24, 31 do out[msb] = {} end
    if block then
        for _, e in ipairs(block.entries) do out[e.msb][e.note] = e.value end
    end
    return out
end

-- entries の (msb, note) を value にした新しい entries。既定の値（ピッチ 64・コーラス 0・ディレイ 0）なら消す。
-- 無ければ、その MSB のまとまりの最後に足す（まとまりが無ければ、MSB_ORDER の順の位置に作る）
function M.set(entries, msb, note, value)
    local out, found = {}, false
    local default = M.PARAMS[msb] and M.PARAMS[msb].default
    local remove = default ~= nil and value == default
    for _, e in ipairs(entries) do
        if e.msb == msb and e.note == note then
            if not found and not remove then out[#out + 1] = {msb = msb, note = note, value = value} end
            found = true
        else
            out[#out + 1] = {msb = e.msb, note = e.note, value = e.value}
        end
    end
    if found or remove then return out end
    -- 足す場所：同じ MSB のまとまりの最後。無ければ、順番が後ろの MSB のまとまりの前（それも無ければ最後）
    local rank = {}
    for i, m in ipairs(M.MSB_ORDER) do rank[m] = i end
    local pos = nil
    for i, e in ipairs(out) do
        if e.msb == msb then pos = i + 1 end
    end
    if not pos then
        for i, e in ipairs(out) do
            if (rank[e.msb] or 99) > (rank[msb] or 99) then pos = i; break end
        end
    end
    table.insert(out, pos or (#out + 1), {msb = msb, note = note, value = value})
    return out
end

-- entries を並べたメッセージ：MSB が変わるたびに CC99、そのあと CC98・CC6
-- ブロックに並べるもの {{msg, adv}, ...}：まとまりごとに（見出しのテキストがあれば、その CC99 と同じ tick に）CC99、CC98・CC6。
-- adv = false は、次と同じ tick（見出し）
function M.layout(entries, ch0, labels)
    local out, msb = {}, nil
    for _, e in ipairs(entries) do
        if e.msb ~= msb then
            if labels and labels[e.msb] then out[#out + 1] = {msg = labels[e.msb], adv = false} end
            out[#out + 1] = {msg = string.char(0xB0 | ch0, 99, e.msb), adv = true}
            msb = e.msb
        end
        out[#out + 1] = {msg = string.char(0xB0 | ch0, 98, e.note), adv = true}
        out[#out + 1] = {msg = string.char(0xB0 | ch0, 6, e.value), adv = true}
    end
    return out
end

function M.messages(entries, ch0)
    local out, msb = {}, nil
    local function cc(n, v) out[#out + 1] = string.char(0xB0 | ch0, n, v) end
    for _, e in ipairs(entries) do
        if e.msb ~= msb then cc(99, e.msb); msb = e.msb end
        cc(98, e.note)
        cc(6, e.value)
    end
    return out
end

-- events のブロックを entries に置き換えた新しい events を返す（ブロックの外は同じ位置のまま）。
-- ブロックは start_ppq から step おき。block が nil なら、最初のノートより前のプログラムチェンジから FROM_PC tick
-- （会社のマクロと同じ）。ただしセットアップの最後のイベントから START_GAP（テイクの ppq に直したもの gap）より近くはしない。
-- 同じ tick の並び：ブロックより前にあったものは前、ブロックは次、ブロックの後ろ（間にはさまっていたノートなども）は後ろ。
-- 次のイベント（ブロックの後ろの最初のもの）とは step あける。ただしそれがノートなら同じ tick まではよい（納品データと同じ）。
-- 足りなければ nil, 理由
local function is_note(msg)
    local st = msg:byte(1)
    return st and st < 0xF0 and ((st & 0xF0) == 0x90 or (st & 0xF0) == 0x80)
end

function M.rewrite(events, block, entries, ch0, step, gap)
    local layout = M.layout(entries, ch0, block and block.labels)
    local member, first, start = {}, nil, nil
    if block then
        for _, k in ipairs(block.members or {}) do member[k] = true end
        if not block.members then for k = block.first, block.last do member[k] = true end end
        first, start = block.first, block.start_ppq
    else
        local first_note = nil
        for i, e in ipairs(events) do
            if is_note_on(e[3]) then first_note = i; break end
        end
        local cut = (first_note or (#events + 1)) - 1
        -- ノートが無いときも、テイクの終わりの「全部のノートを止める」（CC123）は後ろに置く
        if not first_note then
            while cut >= 1 and events[cut][3]:byte(2) == 123 and (events[cut][3]:byte(1) & 0xF0) == 0xB0 do cut = cut - 1 end
        end
        first = cut + 1
        start = (cut >= 1 and events[cut][1] or 0) + gap
        local pc = nil
        for i = 1, cut do
            if events[i][3]:byte(1) == (0xC0 | ch0) then pc = events[i][1] end
        end
        if pc then start = math.max(start, pc + M.FROM_PC * step / M.DELIVERY_STEP) end
    end
    -- 次のイベント：ブロックの始まりより後ろの、ブロックでない最初のもの
    local next_ev = nil
    for k = first, #events do
        if not member[k] then next_ev = events[k]; break end
    end
    -- 置く位置：見出しは次の CC99 と同じ tick（進まない）、ほかは step ずつ
    local pos, place = start, {}
    for i, item in ipairs(layout) do
        place[i] = pos
        if item.adv then pos = pos + step end
    end
    local last_ppq = place[#place] or start
    if #layout > 0 and next_ev then
        local room = next_ev[1] - last_ppq
        local need = is_note(next_ev[3]) and 0 or step
        if room < need then
            return nil, string.format("ブロックの後ろの空きが足りません（あと %d tick 要る）。" ..
                "詰めたあとのデータでは、今ある値は変えられますが、新しく足すことはできません（足すのは詰める前に）",
                math.floor(need - room + 0.5))
        end
    end
    -- 並べる：{ppq, 組（0 = 前、1 = ブロック、2 = 後ろ）, 元の順}
    local rows = {}
    for k, e in ipairs(events) do
        if not member[k] then rows[#rows + 1] = {e = e, ppq = e[1], group = (k < first) and 0 or 2, order = k} end
    end
    for i, item in ipairs(layout) do
        rows[#rows + 1] = {e = {place[i], 0, item.msg}, ppq = place[i], group = 1, order = i}
    end
    table.sort(rows, function(a, b)
        if a.ppq ~= b.ppq then return a.ppq < b.ppq end
        if a.group ~= b.group then return a.group < b.group end
        return a.order < b.order
    end)
    local out = {}
    for i, row in ipairs(rows) do out[i] = row.e end
    return out
end

-- (msb, note) がブロックにあれば、その CC6 の値だけを変えた新しい events（位置も並びもそのまま）。無ければ nil
function M.change_in_place(events, block, msb, note, value)
    if not block then return nil end
    local hit = nil
    for _, e in ipairs(block.entries) do
        if e.msb == msb and e.note == note then hit = e end  -- 同じものが2つあれば後のもの（効いているほう）
    end
    if not hit or not hit.idx then return nil end
    local out = {}
    for k, e in ipairs(events) do out[k] = e end
    local old = events[hit.idx]
    out[hit.idx] = {old[1], old[2], string.char(old[3]:byte(1), 6, value)}
    return out
end

-- RPN / NRPN を順に見て、ドラムの値 {[msb] = {[note] = 値}} を求める（msgs は鳴る順のメッセージ。今の位置の値を見るため）
function M.values_from_messages(msgs, ch0)
    local out = {}
    for msb = 24, 31 do out[msb] = {} end
    local msb, note = nil, nil
    for _, m in ipairs(msgs) do
        local st, d1, d2 = m:byte(1, 3)
        if st and st >= 0xB0 and st <= 0xBF and d1 then
            if d1 == 99 then msb = d2 elseif d1 == 98 then note = d2
            elseif d1 == 101 or d1 == 100 then msb, note = nil, nil
            elseif d1 == 6 and msb and DRUM_MSB[msb] and note then out[msb][note] = d2 end
        end
    end
    return out
end

-- ---------------------------------------------------------------- MIDI_GetAllEvts の中身
function M.decode(buf)
    local out, pos, ppq = {}, 1, 0
    while pos <= #buf - 8 do
        local offset, flags, msg
        offset, flags, msg, pos = string.unpack("<i4Bs4", buf, pos)
        ppq = ppq + offset
        out[#out + 1] = {ppq, flags, msg}
    end
    return out
end

function M.encode(events)
    local parts, prev = {}, 0
    for _, e in ipairs(events) do
        local p = math.floor(e[1] + 0.5)
        parts[#parts + 1] = string.pack("<i4Bs4", p - prev, e[2], e[3])
        prev = p
    end
    return table.concat(parts)
end

return M
