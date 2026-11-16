-- 再生を始める位置で、音源がなっているはずの状態（CC・プログラム・SysEx）を求める（チェイス）。
-- 全部を送り直すのではなく、コントローラーごと・SysEx のアドレスごとに「最後の値」だけを、元の順番で送る。
-- 元の順番で送るので、あとから来たメッセージの副作用（EFX の種類を変えるとパラメータが初期値に戻る、
-- CC121 で全部のコントローラーが戻る、など）も元のとおりになる。
local sysex = dofile(debug.getinfo(1, "S").source:match("^@?(.*[\\/])") .. "xl_sysex.lua")

local M = {}

-- REAPER の MIDI_GetAllEvts の中身を読む。ppq_from 以上 ppq_to 以下の、ノート以外・ミュートでない出来事を
-- 順に {ppq, msg（文字列）} で返す。ppq_to を過ぎたら読むのをやめる（後ろは読まない）。
function M.read_allevts(buf, ppq_from, ppq_to)
    local out = {}
    local pos, ppq, n = 1, 0, #buf
    while pos <= n - 8 do
        local offset, flags, msg
        offset, flags, msg, pos = string.unpack("<i4Bs4", buf, pos)
        ppq = ppq + offset
        if ppq > ppq_to then break end
        if ppq >= ppq_from and (flags & 2) == 0 and #msg > 0 then
            local st = msg:byte(1)
            local kind = st & 0xF0
            if kind ~= 0x80 and kind ~= 0x90 and st ~= 0xFF then
                out[#out + 1] = {ppq, msg}
            end
        end
    end
    return out
end

local SKIP_CC = {[120] = true, [123] = true}

-- ふつうの CC：ほかのメッセージと関係なく、それだけで値が決まるもの（送る順番を気にしなくてよい）。
-- バンク（0, 32）、データエントリ（6, 38, 96, 97）、RPN / NRPN を選ぶもの（98〜101）、モードの CC（120〜127）は入らない
function M.is_plain_cc(n)
    return not (n == 0 or n == 32 or n == 6 or n == 38 or (n >= 96 and n <= 101) or n >= 120)
end

-- GS のリセットのあとの、ふつうの CC の値（分からないものは入れない）
M.CC_DEFAULT = {[1] = 0, [5] = 0, [7] = 100, [10] = 64, [11] = 127, [64] = 0, [65] = 0, [66] = 0, [67] = 0,
                [71] = 64, [72] = 64, [73] = 64, [74] = 64, [75] = 64, [91] = 40, [93] = 0, [94] = 0}  -- 全部の音を止める・ノートを止めるは送らない
local SELECT_CC = {[98] = "nrpn", [99] = "nrpn", [100] = "rpn", [101] = "rpn"}
local DATA_CC = {[6] = true, [38] = true, [96] = true, [97] = true}

-- 1つのチャンネル（トラック）のメッセージ（並んだ文字列）から、送り直すメッセージ（文字列）を作る。
-- ch はトラック名から決めたチャンネル（1〜16）。元のチャンネルは使わない（X-Laben への送りで変わるため）。
-- プログラムチェンジは、そのとき効いていたバンク（CC0 / CC32）と一緒に、バンク → プログラムの順で送る
-- （GM / GS では、バンクはプログラムチェンジを受けたときに決まる。あとから CC0 / CC32 だけ来ても音色は変わらない）。
-- RPN / NRPN の値（データエントリ。ドラムの音ごとの設定など）は、選んでいたパラメータごとに覚え、選ぶ CC と一緒に送る。
-- ピッチベンドは真ん中に戻す。
function M.chase_channel(msgs, ch)
    local status_ch = ch - 1
    local last, order = {}, {}
    local sel = {rpn = {}, nrpn = {}, mode = nil}
    local bank = {}
    local function keep(key, seq, kind, bytes, extra)
        if not last[key] then order[#order + 1] = key end
        last[key] = {seq = seq, kind = kind, bytes = bytes, extra = extra}
    end
    for seq, msg in ipairs(msgs) do
        local st, d1, d2 = msg:byte(1, 3)
        local kind = st & 0xF0
        if kind == 0xB0 and d1 and not SKIP_CC[d1] then
            if SELECT_CC[d1] then
                local mode = SELECT_CC[d1]
                sel.mode = mode
                sel[mode][(d1 == 101 or d1 == 99) and 1 or 2] = d2
                keep("sel" .. d1, seq, "sel", {d1, d2})
            elseif DATA_CC[d1] and sel.mode then
                local s = sel[sel.mode]
                local key = string.format("de:%s:%s:%s:%d", sel.mode, tostring(s[1]), tostring(s[2]), d1)
                keep(key, seq, "data", {d1, d2}, {mode = sel.mode, msb = s[1], lsb = s[2]})
            else
                if d1 == 0 then bank.msb = d2 elseif d1 == 32 then bank.lsb = d2 end
                keep("cc" .. d1, seq, "cc", {d1, d2})
            end
        elseif kind == 0xC0 and d1 then
            keep("pc", seq, "pc", {d1}, {msb = bank.msb, lsb = bank.lsb})
        elseif kind == 0xD0 and d1 then
            keep("cp", seq, "cp", {d1})
        end
    end
    local entries, selects = {}, {}
    for _, key in ipairs(order) do
        local e = last[key]
        if e.kind == "sel" then selects[#selects + 1] = e else entries[#entries + 1] = e end
    end
    table.sort(entries, function(a, b) return a.seq < b.seq end)
    table.sort(selects, function(a, b) return a.seq < b.seq end)
    local out = {}
    local function cc(n, v) out[#out + 1] = string.char(0xB0 | status_ch, n, v) end
    for _, e in ipairs(entries) do
        local x = e.extra
        if e.kind == "data" then
            if x.mode == "rpn" then
                if x.msb then cc(101, x.msb) end
                if x.lsb then cc(100, x.lsb) end
            else
                if x.msb then cc(99, x.msb) end
                if x.lsb then cc(98, x.lsb) end
            end
            cc(e.bytes[1], e.bytes[2])
        elseif e.kind == "pc" then
            if x.msb then cc(0, x.msb) end
            if x.lsb then cc(32, x.lsb) end
            out[#out + 1] = string.char(0xC0 | status_ch, e.bytes[1])
        elseif e.kind == "cp" then
            out[#out + 1] = string.char(0xD0 | status_ch, e.bytes[1])
        else
            cc(e.bytes[1], e.bytes[2])
        end
    end
    for _, e in ipairs(selects) do cc(e.bytes[1], e.bytes[2]) end
    out[#out + 1] = string.char(0xE0 | status_ch, 0x00, 0x40)
    return out
end

-- SysEx のトラックのメッセージ（並んだ文字列）から、送り直すメッセージを作る。
-- 音源全体のリセットがあれば、その前のものは捨て、リセットを最初に送る。
-- X-Laben の形はアドレスごとに最後のものだけ、知らない形は全部を、元の順番で送る。
function M.chase_sysex(msgs)
    local reset, last, others = nil, {}, {}
    for seq, msg in ipairs(msgs) do
        if msg:byte(1) == 0xF0 then
            if sysex.is_reset(msg) then
                reset, last, others = {seq = seq, msg = msg}, {}, {}
            else
                local key = sysex.key(msg)
                if key then
                    last[key] = {seq = seq, msg = msg}
                else
                    others[#others + 1] = {seq = seq, msg = msg}
                end
            end
        end
    end
    local list = others
    for _, e in pairs(last) do list[#list + 1] = e end
    table.sort(list, function(a, b) return a.seq < b.seq end)
    local out = {}
    if reset then out[1] = reset.msg end
    for _, e in ipairs(list) do out[#out + 1] = e.msg end
    return out
end

-- ------------------------------------------------------------------ 違うものだけ送る
-- JSFX が値を覚える RPN / NRPN（データエントリ CC6）：NRPN の MSB 1 と 24〜31、RPN の MSB 0 の LSB 0〜2
function M.param_tracked(mode, msb, lsb)
    if lsb == nil or msb == nil then return false end
    if mode == "nrpn" then return msb == 1 or (msb >= 24 and msb <= 31) end
    return mode == "rpn" and msb == 0 and lsb >= 0 and lsb <= 2
end

-- GS のリセット（とキットの切り替え）のあとの値。分からないものは nil
-- ドラムのピッチ（24）は真ん中、コーラス（30）・ディレイ（31）は 0。レベル・パン・リバーブ（26・28・29）は音ごとに違う。
-- パートの NRPN（MSB 1）は真ん中。RPN 0（ピッチベンドの幅）は 2、RPN 1・2（チューニング）は真ん中
function M.param_default(mode, msb, lsb)
    if mode == "nrpn" then
        if msb == 24 or msb == 1 then return 64 elseif msb == 30 or msb == 31 then return 0 end
    elseif mode == "rpn" and msb == 0 then
        return ({[0] = 2, [1] = 64, [2] = 64})[lsb]
    end
end

local function param_key(mode, msb, lsb) return string.format("%s:%d:%d", mode, msb, lsb) end

-- 選ぶ CC（NRPN は 99 / 98、RPN は 101 / 100）
local function select_msgs(status_ch, mode, msb, lsb)
    if mode == "rpn" then
        return string.char(0xB0 | status_ch, 101, msb), string.char(0xB0 | status_ch, 100, lsb)
    end
    return string.char(0xB0 | status_ch, 99, msb), string.char(0xB0 | status_ch, 98, lsb)
end
-- 全部を送るときの並び（{msg, 待つミリ秒} の表）を、2つに分ける：
--   sig   ふつうの CC とピッチベンド以外（SysEx、バンク、プログラム、RPN/NRPN など）を順につないだ文字列。
--         同じ sig なら、音源のその部分の状態は同じ（順番まで含めて同じメッセージを送ることになるため）
--   plain ふつうの CC の、チャンネルごとの最後の値 {[ch] = {[番号] = 値}}
--   param JSFX が覚える RPN / NRPN の値 {[ch] = {[鍵] = {mode, msb, lsb, value}}}（sig には入れない）
--   sel   最後に選んでいる RPN / NRPN {[ch] = {mode, msb, lsb}}（sig には入れない）
--   has_reset 音源全体のリセットが入っているか
-- 覚えない RPN / NRPN の値は、そのとき選んでいたものと一緒に sig に入れる
function M.plan(schedule)
    local sig, plain, param, sel, has_reset = {}, {}, {}, {}, false
    for _, e in ipairs(schedule) do
        local m = e[1]
        local st = m:byte(1)
        if st == 0xF0 then
            sig[#sig + 1] = m
            if sysex.is_reset(m) then has_reset = true end
        else
            local kind, ch = st & 0xF0, (st & 0x0F) + 1
            local n, v = m:byte(2), m:byte(3)
            if kind == 0xB0 and SELECT_CC[n] then
                local mode = SELECT_CC[n]
                local s = sel[ch]
                if not s or s.mode ~= mode then s = {mode = mode}; sel[ch] = s end
                if n == 99 or n == 101 then s.msb = v else s.lsb = v end
            elseif kind == 0xB0 and n == 6 and sel[ch] and M.param_tracked(sel[ch].mode, sel[ch].msb, sel[ch].lsb) then
                local s = sel[ch]
                param[ch] = param[ch] or {}
                param[ch][param_key(s.mode, s.msb, s.lsb)] = {mode = s.mode, msb = s.msb, lsb = s.lsb, value = v}
            elseif kind == 0xB0 and M.is_plain_cc(n) then
                plain[ch] = plain[ch] or {}
                plain[ch][n] = v
            elseif kind == 0xB0 and DATA_CC[n] then
                local s = sel[ch] or {}
                sig[#sig + 1] = string.format("[%d %s %s %s]", ch, tostring(s.mode), tostring(s.msb), tostring(s.lsb)) .. m
            elseif kind ~= 0xE0 then
                sig[#sig + 1] = m
            end
        end
    end
    return {sig = table.concat(sig), sig_list = sig, plain = plain, param = param, sel = sel, has_reset = has_reset}
end

-- 文字列の短い印（FNV-1a、32 ビット）
function M.hash(s)
    local h = 2166136261
    for i = 1, #s do
        h = ((h ~ s:byte(i)) * 16777619) & 0xFFFFFFFF
    end
    return string.format("%08x:%d", h, #s)
end

-- 音源の今の CC（read_cc(ch, 番号) → 値、-1 = リセットのあとの既定のまま）と、行き先の plan を比べ、
-- 違うふつうの CC だけのメッセージ（文字列）を返す。channels はトラックのあるチャンネル。ピッチベンドは真ん中に戻す。
-- read_param(ch, mode, msb, lsb) → 音源の RPN / NRPN の値（-1 = 分からない）、read_sel(ch) → 今選んでいるもの。
-- 渡されれば、覚える RPN / NRPN も、違うものだけ「選ぶ CC → データ」で送り、最後に行き先と同じものを選び直す。
-- 既定の値が分からない CC・パラメーターを戻す必要があるときは nil（全部を送り直す）
function M.diff(plan, channels, read_cc, read_param, read_sel)
    local out = {}
    if read_param then
        for _, ch in ipairs(channels) do
            local msgs = M.diff_params(plan, ch, read_param, read_sel)
            if not msgs then return nil end
            for _, m in ipairs(msgs) do out[#out + 1] = m end
        end
    end
    for _, ch in ipairs(channels) do
        local want = plan.plain[ch] or {}
        for n = 0, 127 do
            if M.is_plain_cc(n) then
                local tv = want[n]          -- 行き先の値（nil = リセットのあとの既定のまま）
                local sv = read_cc(ch, n)   -- 音源の値（-1 = リセットのあとの既定のまま）
                local d = M.CC_DEFAULT[n]
                if tv ~= nil then
                    local s_eff = sv >= 0 and sv or d   -- 既定が分からなければ nil → 違うとみなして送る
                    if s_eff ~= tv then out[#out + 1] = string.char(0xB0 | (ch - 1), n, tv) end
                elseif sv >= 0 then
                    -- 行き先は既定のまま。既定が分からなければ戻せないので、全部を送り直す
                    if d == nil then return nil end
                    if sv ~= d then out[#out + 1] = string.char(0xB0 | (ch - 1), n, d) end
                end
            end
        end
        out[#out + 1] = string.char(0xE0 | (ch - 1), 0x00, 0x40)
    end
    return out
end

-- 覚える RPN / NRPN の、1つのチャンネルの違い（メッセージの並び）。戻せないものがあれば nil
function M.diff_params(plan, ch, read_param, read_sel)
    local status_ch = ch - 1
    local want = plan.param[ch] or {}
    local out = {}
    local function put(mode, msb, lsb, v)
        local a, b = select_msgs(status_ch, mode, msb, lsb)
        out[#out + 1] = a
        out[#out + 1] = b
        out[#out + 1] = string.char(0xB0 | status_ch, 6, v)
    end
    -- 行き先にあるもの（並びをそろえるため、鍵の順に）
    local keys = {}
    for k in pairs(want) do keys[#keys + 1] = k end
    table.sort(keys)
    for _, k in ipairs(keys) do
        local p = want[k]
        if read_param(ch, p.mode, p.msb, p.lsb) ~= p.value then put(p.mode, p.msb, p.lsb, p.value) end
    end
    -- 音源にだけあるもの：既定に戻す（分からなければ全部を送り直す）
    local function back(mode, msb, lsb)
        if want[param_key(mode, msb, lsb)] then return true end
        local sv = read_param(ch, mode, msb, lsb)
        if not sv or sv < 0 then return true end
        local d = M.param_default(mode, msb, lsb)
        if d == nil then return false end
        if sv ~= d then put(mode, msb, lsb, d) end
        return true
    end
    for _, msb in ipairs({1, 24, 25, 26, 27, 28, 29, 30, 31}) do
        for lsb = 0, 127 do
            if not back("nrpn", msb, lsb) then return nil end
        end
    end
    for lsb = 0, 2 do
        if not back("rpn", 0, lsb) then return nil end
    end
    -- 最後に、行き先と同じものを選ぶ（行き先で何も選んでいなければ「なし」= RPN 127 / 127）
    local ts = plan.sel[ch]
    local mode, msb, lsb = "rpn", 127, 127
    if ts and ts.mode and ts.msb and ts.lsb then mode, msb, lsb = ts.mode, ts.msb, ts.lsb end
    local cm, cmsb, clsb = nil, nil, nil
    if read_sel then cm, cmsb, clsb = read_sel(ch) end
    if #out > 0 or cm ~= mode or cmsb ~= msb or clsb ~= lsb then
        local a, b = select_msgs(status_ch, mode, msb, lsb)
        out[#out + 1] = a
        out[#out + 1] = b
    end
    return out
end

return M
