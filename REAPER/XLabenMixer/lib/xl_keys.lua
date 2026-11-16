-- ミキサーの画面に入力があるときも、ミキサーに割り当てたショートカットで閉じられるようにする。
-- 画面（gfx）がアクティブな間は、REAPER にキーが届かないので、REAPER のキーの割り当て（reaper-kb.ini）を読み、
-- このスクリプトに割り当てられたキーが押されたら、ミキサー自身が閉じる。
-- reaper-kb.ini の行：KEY <フラグ> <キー> <コマンド> <セクション>。フラグは 1 = 仮想キー、4 = Shift、8 = Ctrl、16 = Alt
local M = {}

-- text（reaper-kb.ini の中身）から、names（コマンドの名前の集合。"_RS…" や番号）に割り当てられたキーの一覧
-- {{vk, shift, ctrl, alt}, ...}。メインのセクション（0）だけ
function M.parse_kb(text, names)
    local out = {}
    for line in (text or ""):gmatch("[^\r\n]+") do
        local flags, key, cmd, sec = line:match("^KEY%s+(%d+)%s+(%d+)%s+(%S+)%s+(%d+)")
        flags, key = tonumber(flags), tonumber(key)
        if flags and names[cmd] and tonumber(sec) == 0 and flags < 32 then
            local vk = key
            if flags & 1 == 0 then
                -- 文字のキー：小文字は大文字の仮想キーにそろえる
                if key >= 97 and key <= 122 then vk = key - 32 end
            end
            out[#out + 1] = {vk = vk, shift = flags & 4 ~= 0, ctrl = flags & 8 ~= 0, alt = flags & 16 ~= 0}
        end
    end
    return out
end

-- gfx.getchar() の値と gfx.mouse_cap から、(仮想キー, shift, ctrl, alt)。分からないキーは vk = nil
function M.from_gfx(c, cap)
    local ctrl, shift, alt = cap & 4 ~= 0, cap & 8 ~= 0, cap & 16 ~= 0
    if alt and c > 256 and c < 512 then c = c - 256 end
    local vk
    if ctrl and c >= 1 and c <= 26 then
        vk = 64 + c
    elseif c >= 97 and c <= 122 then
        vk = c - 32
    elseif c >= 65 and c <= 90 then
        vk = c
    elseif c >= 48 and c <= 57 then
        vk = c
    elseif c == 32 or c == 13 or c == 27 or c == 9 or c == 8 then
        vk = c
    else
        -- F1〜F12：gfx は 'f1'〜'f12' の文字をまとめた数を返す
        for n = 1, 12 do
            local code = n < 10 and (0x66 * 256 + 48 + n) or (0x66 * 65536 + 49 * 256 + 48 + n - 10)
            if c == code then vk = 111 + n end
        end
    end
    return vk, shift, ctrl, alt
end

-- 押されたキーが一覧のどれかと同じか
function M.matches(bindings, c, cap)
    local vk, shift, ctrl, alt = M.from_gfx(c, cap)
    if not vk then return false end
    for _, b in ipairs(bindings) do
        if b.vk == vk and b.shift == shift and b.ctrl == ctrl and b.alt == alt then return true end
    end
    return false
end

-- ------------------------------------------------------------------ となりのスクリプト（ミキサー・ドラム・EFXUI）
-- 画面がアクティブな間は REAPER にキーが届かないので、ほかの 2 つに割り当てたショートカットも、この画面で受けて実行する。
-- スクリプトはファイル名で見分ける（reaper-kb.ini の SCR 行：SCR <フラグ> <セクション> <名前> "<説明>" <パス>）
-- 窓の種類ごとのスクリプトのファイル名（同じ窓を開くアクションが2つあることがある：ドラムの前の名前 SC_Drum_Macro.lua）
M.KINDS = {
    mixer = {"xlaben_mixer.lua"},
    drum = {"xlaben_drum.lua", "sc_drum_macro.lua"},
    efx = {"xlaben_efx_controller.lua"},
    strum = {"xlaben_strum.lua", "strumeditor_v2.lua"},   -- オートストラマー（前の名前 StrumEditor_V2.lua）
}

-- text（reaper-kb.ini）から、ファイル名が files のどれかのスクリプトのコマンドの名前（"_RS…"）の集合
function M.script_names(text, files)
    local want = {}
    for _, f in ipairs(files) do want[f] = true end
    local names = {}
    for line in (text or ""):gmatch("[^\r\n]+") do
        local id, rest = line:match("^SCR%s+%d+%s+%d+%s+(%S+)%s+(.*)$")
        if id then
            rest = rest:gsub('^".-"%s*', "")                       -- 説明
            local path = rest:match('^"(.-)"') or rest:match("^(%S+)") or ""
            local file = (path:match("[^/\\]+$") or ""):lower()
            if want[file] then names["_" .. id] = true end
        end
    end
    return names
end

-- この窓（kind）のショートカット（own：自分のアクションの名前も足す）と、ほかの窓のショートカット（cmd 付き）
function M.load(text, kind, own)
    local mine = {}
    for k in pairs(own or {}) do mine[k] = true end
    for k in pairs(M.script_names(text, M.KINDS[kind] or {})) do mine[k] = true end
    local others = {}
    for k, files in pairs(M.KINDS) do
        if k ~= kind then
            for name in pairs(M.script_names(text, files)) do if not mine[name] then others[name] = true end end
        end
    end
    return M.parse_kb(text, mine), M.parse_named(text, others)
end

-- text（reaper-kb.ini）から、names に割り当てられたキーの一覧（parse_kb と同じ形に、cmd = コマンドの名前 を足す）
function M.parse_named(text, names)
    local out = {}
    for line in (text or ""):gmatch("[^\r\n]+") do
        local cmd = line:match("^KEY%s+%d+%s+%d+%s+(%S+)%s+%d+")
        if cmd and names[cmd] then
            local one = M.parse_kb(line, {[cmd] = true})[1]
            if one then one.cmd = cmd; out[#out + 1] = one end
        end
    end
    return out
end

-- 押されたキーが、となりのスクリプトのショートカットなら、そのコマンドの名前（"_RS…"）
function M.match_other(list, c, cap)
    local vk, shift, ctrl, alt = M.from_gfx(c, cap)
    if not vk then return nil end
    for _, b in ipairs(list) do
        if b.vk == vk and b.shift == shift and b.ctrl == ctrl and b.alt == alt then return b.cmd end
    end
end

-- となりのスクリプトのショートカットが押されたら、REAPER と同じくそのアクションを実行する。実行したら true
function M.run_other(r, list, c, cap)
    local cmd = M.match_other(list, c, cap)
    if not cmd then return false end
    local id = r.NamedCommandLookup(cmd)
    if id and id > 0 then r.Main_OnCommand(id, 0) end
    return true
end

-- 動いているスクリプト自身のコマンドの名前の集合（reaper-kb.ini の KEY 行で使われる形）
function M.own_names(r)
    local names = {}
    local _, _, _, cmd = r.get_action_context()
    if cmd and cmd > 0 then
        names[tostring(cmd)] = true
        local named = r.ReverseNamedCommandLookup(cmd)
        if named and named ~= "" then
            names["_" .. named] = true
            names[named] = true
        end
    end
    return names
end

return M
