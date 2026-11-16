-- @description X-Laben ツール：インストール・更新
-- @version 5.0
--
-- これ 1 つで「入れる」「更新する」「入れる場所を変える」をする。実行すると画面が出る。
--   ・初めて：このファイルを REAPER で実行する（アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む）。
--     「インストールする」を押すと、REAPER の Scripts フォルダの中の XLabenTools フォルダに全部入り、アクションに登録される。
--     入れる前に「場所を変える」で別の場所を選べる。
--   ・更新：アクション「XLaben_Update.lua」を実行して「更新する」を押す。新しい版があれば入れ替える。
--   ・データの ZIP（GitHub に置けない大きいもの：Python・モデル・MIDIChecker）：XLabenTools フォルダに ZIP を置いて、これを実行すると、
--     自動で入って ZIP は消える。データはめったに変わらないので、変わったときだけ配る。コードはいつも GitHub から。
--   ・前に渡したマクロ（EFXUI・ドラム・ミキサー・オートストラマーなど）：新しいものに置き換える。ショートカットとツールバーはそのまま。
--     前のファイルは XLabenTools\old_macros\<日付> へ移す。REAPER の登録（reaper-kb.ini）は REAPER が開いている間は書き換えられないので、
--     REAPER を閉じたときに裏で書き換える（PowerShell）。開発中のフォルダ（git の中）のマクロには触らない。
--   ・場所を変える：入れたあとでも、フォルダごと別の場所へ移せる（ショートカットは、REAPER を開き直すとそのまま使える）。
-- 入れ替えるのは、配っているファイルだけ（MANIFEST.txt に書いてあるもの）。次のものには触らない：
--   user フォルダ、ログ、自分で保存したプリセット、A2M の python・models・MIDIChecker（データの ZIP から入る）。
-- 使うもの：Windows に最初から入っている curl.exe・tar.exe・PowerShell。git は要らない。
-- 配る元が非公開のときは、入れる場所の user\github_token.txt に読み取り専用のトークンを1行で置く（表示もログにも書かない）。

local r = reaper
local M = {}

M.SOURCE = {repo = "nemico/xl-tools", branch = "main"}  -- 配る元（GitHub の owner/name）。scripts/make_dist.py が配布のときに書き込む
M.TITLE = "X-Laben ツール：インストール・更新"
M.EXT = "XLabenTools"       -- REAPER に覚えておく名前（入れる場所）
M.FOLDER = "XLabenTools"    -- 専用のフォルダの名前
M.HERE = (((debug.getinfo(1, "S").source:match("^@(.*[\\/])")) or ""):gsub("/", "\\"))  -- このファイルがあるフォルダ
M.TIMEOUT_MS = 320000       -- ダウンロードを待つ上限
M.BASE_DIRS = {"A2M\\python", "A2M\\models", "A2M\\MIDIChecker"}  -- データの ZIP から入るもの（更新では触らない）

local function sysexe(name)
    return (os.getenv("SystemRoot") or "C:\\Windows") .. "\\System32\\" .. name
end

local function win(path) return (path:gsub("/", "\\")) end

local function trim(s)
    if not s then return nil end
    s = s:gsub("^\239\187\191", ""):gsub("^%s+", ""):gsub("%s+$", "")
    if s == "" then return nil end
    return s
end

-- フォルダのパスを「\ で終わる形」にそろえる
local function as_dir(path)
    path = win(trim(path) or "")
    if path == "" then return "" end
    return (path:gsub("\\+$", "")) .. "\\"
end
M.as_dir = as_dir

local function parent_dir(path) return path:match("^(.*\\)[^\\]+\\?$") or "" end

function M.read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local data = f:read("*a")
    f:close()
    return data
end

function M.write_file(path, data)
    local dir = path:match("^(.*)[\\/][^\\/]+$")
    if dir then r.RecursiveCreateDirectory(dir, 0) end
    local f, err = io.open(path, "wb")
    if not f then return false, err end
    f:write(data)
    f:close()
    return true
end

-- フォルダがあって、中に何かあるか
function M.dir_has_content(path)
    path = path:gsub("\\$", "")
    r.EnumerateFiles(path, -1)
    r.EnumerateSubdirectories(path, -1)
    return (r.EnumerateFiles(path, 0) or r.EnumerateSubdirectories(path, 0)) ~= nil
end

-- (終了コード, 出力)。REAPER の ExecProcess は「終了コード\n出力」を返す。失敗・時間切れは nil
function M.exec(cmd, timeout_ms)
    local out = r.ExecProcess(cmd, timeout_ms or 120000)
    if not out then return -1, "" end
    local code, rest = out:match("^(%-?%d+)%s*\n?(.*)$")
    return tonumber(code) or -1, rest or ""
end

-- 版の番号（年.月.日[.n]）をくらべる：a が b より新しいか
function M.version_gt(a, b)
    if not a then return false end
    if not b then return true end
    local pa, pb = {}, {}
    for n in a:gmatch("%d+") do pa[#pa + 1] = tonumber(n) end
    for n in b:gmatch("%d+") do pb[#pb + 1] = tonumber(n) end
    for i = 1, math.max(#pa, #pb) do
        local x, y = pa[i] or 0, pb[i] or 0
        if x ~= y then return x > y end
    end
    return false
end

------------------------------------------------------------------------------ 入れる場所
function M.is_install(dir) return dir ~= "" and r.file_exists(dir .. "MANIFEST.txt") end

function M.default_root() return as_dir(r.GetResourcePath()) .. "Scripts\\" .. M.FOLDER .. "\\" end

-- 入れる場所：このファイルが入れてあるフォルダの中にあればそこ。無ければ、前に選んだ場所。それも無ければ Scripts\XLabenTools
function M.resolve_root()
    if M.is_install(M.HERE) then return M.HERE end
    local saved = as_dir(r.GetExtState(M.EXT, "root"))
    if saved ~= "" then return saved end
    return M.default_root()
end

function M.set_root(root) r.SetExtState(M.EXT, "root", root, true) end

-- 英数字と記号だけのパスか（AI の下書きは、日本語などが入ったパスでは動かない）
function M.ascii_path(path) return not path:find("[\128-\255]") end

------------------------------------------------------------------------------ 取ってくる
-- 非公開の元から取るときのトークン（user\github_token.txt の1行目）。無ければ nil
function M.token(root)
    local line = (M.read_file(root .. "user\\github_token.txt") or ""):match("^[^\r\n]*")
    return trim(line)
end

function M.version_url()
    return "https://api.github.com/repos/" .. M.SOURCE.repo .. "/contents/VERSION.txt?ref=" .. M.SOURCE.branch
end

function M.zip_url()
    return "https://api.github.com/repos/" .. M.SOURCE.repo .. "/zipball/" .. M.SOURCE.branch
end

-- url を out_path に保存する。うまくいけば true、だめなら false と理由
function M.curl(url, out_path, accept, root)
    local cmd = string.format('"%s" -L -sS --fail --connect-timeout 15 --max-time 300 -o "%s"', sysexe("curl.exe"), out_path)
    if accept then cmd = cmd .. ' -H "Accept: ' .. accept .. '"' end
    local token = root and M.token(root)
    if token and url:match("^https://api%.github%.com/") then cmd = cmd .. ' -H "Authorization: Bearer ' .. token .. '"' end
    local code, out = M.exec(cmd .. ' "' .. url .. '"', M.TIMEOUT_MS)
    if code ~= 0 then return false, string.format("curl %d %s", code, trim(out) or "") end
    return true
end

-- ZIP を dir に展開し、いちばん上のフォルダ（GitHub の ZIP は「owner-name-番号」、データの ZIP は XLabenTools が1つ）を返す
function M.extract(zip, dir, timeout_ms)
    r.RecursiveCreateDirectory(dir, 0)
    local code, out = M.exec(string.format('"%s" -xf "%s" -C "%s"', sysexe("tar.exe"), zip, dir), timeout_ms or 120000)
    if code ~= 0 then return nil, string.format("tar %d %s", code, trim(out) or "") end
    r.EnumerateSubdirectories(dir, -1)  -- 前に見た結果を忘れさせる
    local top = r.EnumerateSubdirectories(dir, 0)
    if not top then return nil, "ZIP の中にフォルダがありません" end
    return dir .. "\\" .. top .. "\\"
end

function M.rmdir(dir)
    M.exec(string.format('"%s" /C rmdir /S /Q "%s"', sysexe("cmd.exe"), (dir:gsub("\\$", ""))), 600000)
end

-- ダウンロードと展開に使う作業用のフォルダ（入れる場所の外。入れると決まるまで、入れる場所には何も作らない）
function M.tmp_dir()
    return (os.getenv("TEMP") or os.getenv("TMP") or "C:\\Windows\\Temp") .. "\\xl_tools_update"
end

-- 配る元の今の版。(版) か (nil, 理由)
function M.latest_version(root)
    local tmp = M.tmp_dir()
    M.rmdir(tmp)
    r.RecursiveCreateDirectory(tmp, 0)
    local ok, err = M.curl(M.version_url(), tmp .. "\\VERSION.txt", "application/vnd.github.raw+json", root)
    local latest = ok and trim(M.read_file(tmp .. "\\VERSION.txt"))
    M.rmdir(tmp)
    if latest and not latest:match("^[%w%.%-_]+$") then latest, err = nil, "版の番号が読めません" end
    if not latest then return nil, err or "版の番号が空です" end
    return latest
end

------------------------------------------------------------------------------ 入れ替える
-- MANIFEST.txt：1行に「大きさ<TAB>印<TAB>パス」。印は - か keep（すでにあれば上書きしない：自分で書き換えるファイル）。
-- パスは / 区切りで、このフォルダの中だけ（.. や : を含む行があれば、全体を受け付けない）
function M.parse_manifest(text)
    local list, set = {}, {}
    for line in (text or ""):gmatch("[^\r\n]+") do
        local size, flag, path = line:match("^(%d+)\t(%S+)\t(.+)$")
        if not size then return nil, "MANIFEST の行が読めません：" .. line end
        if path:find("..", 1, true) or path:find(":", 1, true) or path:find("\\", 1, true) or path:match("^/") then
            return nil, "MANIFEST のパスがこのフォルダの外を指しています：" .. path
        end
        local e = {path = path, size = tonumber(size), keep = flag == "keep"}
        list[#list + 1] = e
        set[path] = e
    end
    return list, set
end

-- 何を写して何を消すか。old_set：今入っている版の一覧（無ければ nil）、new_list：新しい版の一覧、exists(path)：今あるか
--   写す：新しい版の全部。ただし keep の印のファイルは、すでにあれば写さない
--   消す：前の版にあって新しい版に無いもの（keep の印だったものは残す）。一覧に無いファイルには触らない
function M.plan(old_set, new_list, exists)
    local copy, remove, new_set = {}, {}, {}
    for _, e in ipairs(new_list) do
        new_set[e.path] = true
        if not (e.keep and exists(e.path)) then copy[#copy + 1] = e end
    end
    for path, e in pairs(old_set or {}) do
        if not new_set[path] and not e.keep then remove[#remove + 1] = path end
    end
    table.sort(remove)
    return copy, remove
end

-- src（展開したフォルダ）の中身が一覧どおりかを確かめながら読む。1つでも違えば nil と理由（何も書き換えない）
function M.load_release(src, copy)
    local data = {}
    for _, e in ipairs(copy) do
        local d = M.read_file(src .. win(e.path))
        if not d then return nil, "ダウンロードした中に無い：" .. e.path end
        if #d ~= e.size then return nil, string.format("大きさが違う：%s（%d、一覧は %d）", e.path, #d, e.size) end
        data[e.path] = d
    end
    return data
end

-- 写して、要らなくなったものを消す。書けなかったパスの一覧を返す（空なら成功）
function M.apply(root, copy, data, remove)
    local failed = {}
    for _, e in ipairs(copy) do
        local ok = M.write_file(root .. win(e.path), data[e.path])
        if not ok then failed[#failed + 1] = e.path end
    end
    if #failed == 0 then
        for _, path in ipairs(remove) do os.remove(root .. win(path)) end
    end
    return failed
end

-- src（配る形のフォルダ）のコードを root に入れる。版の番号と一覧は最後に書く（途中で止まっても、もう一度でやり直せる）。
-- 成功：{copied, removed, to}。だめ：nil, 理由, 何も書き換えていないか
function M.apply_source(root, src)
    local manifest = M.read_file(src .. "MANIFEST.txt")
    if not manifest then return nil, "中に MANIFEST.txt がありません", true end
    local new_list, err = M.parse_manifest(manifest)
    if not new_list then return nil, err, true end
    local old_list, old_set = M.parse_manifest(M.read_file(root .. "MANIFEST.txt") or "")
    if not old_list then old_set = nil end  -- 今入っている版の一覧が無い・読めないときは、消すものは無し
    local copy, remove = M.plan(old_set, new_list, function(path) return r.file_exists(root .. win(path)) end)
    local data
    data, err = M.load_release(src, copy)
    if not data then return nil, err, true end
    local failed = M.apply(root, copy, data, remove)
    if #failed == 0 then
        local a = M.write_file(root .. "MANIFEST.txt", manifest)
        local b = M.write_file(root .. "VERSION.txt", M.read_file(src .. "VERSION.txt") or "")
        if not (a and b) then failed[#failed + 1] = "VERSION.txt / MANIFEST.txt" end
    end
    if #failed > 0 then return nil, "書けなかった：\n" .. table.concat(failed, "\n"), false end
    return {copied = #copy, removed = #remove, to = trim(M.read_file(src .. "VERSION.txt"))}
end

------------------------------------------------------------------------------ アクション
-- actions.txt：1行に「パス<TAB>説明<TAB>前の名前<TAB>一緒に動かすファイル<TAB>引き継ぐファイル」（後ろの 3 つは , 区切りで、無くてもよい）
--   前の名前：前に渡したマクロのファイル名（これが別の場所で登録されていたら、このアクションに置き換える）
--   一緒に動かすファイル：前のマクロのとなりにあれば、前のマクロと一緒に old_macros へ動かす
--   引き継ぐファイル：前のマクロのとなりにあれば、新しいほうのとなりへ写す（プリセットなど。1つの元につき1回だけ）
local function split_list(s)
    local out = {}
    for item in (s or ""):gmatch("[^,]+") do
        local x = trim(item)
        if x then out[#out + 1] = x end
    end
    return out
end

function M.actions(root)
    local out = {}
    for line in (M.read_file(root .. "actions.txt") or ""):gmatch("[^\r\n]+") do
        local f = {}
        for field in (line .. "\t"):gmatch("([^\t]*)\t") do f[#f + 1] = field end
        if f[1] and f[1] ~= "" and f[2] then
            local names = {[f[1]:match("[^/]+$"):lower()] = true}
            for _, n in ipairs(split_list(f[3])) do names[n:lower()] = true end
            out[#out + 1] = {path = f[1], label = f[2], names = names, companions = split_list(f[4]), takeover = split_list(f[5])}
        end
    end
    return out
end

-- actions.txt のスクリプトをアクションに登録する。もう登録してあれば何も変わらない。
-- (登録した説明の一覧, 見つからなかったパスの一覧, {フルパス = "RS…"})
function M.register(root)
    local done, missing, ids = {}, {}, {}
    for _, a in ipairs(M.actions(root)) do
        local full = root .. win(a.path)
        if r.file_exists(full) then
            local cmd = r.AddRemoveReaScript(true, 0, full, true)
            if cmd and cmd ~= 0 then ids[full] = r.ReverseNamedCommandLookup(cmd) end
            done[#done + 1] = string.format("・%s（%s）", a.label, a.path:match("[^/]+$"))
        else
            missing[#missing + 1] = a.path
        end
    end
    return done, missing, ids
end

-- REAPER に登録してあるスクリプト（reaper-kb.ini の SCR の行）：{path, name = 小文字のファイル名, id = "RS…", keys = ショートカットがあるか}
function M.kb_scripts()
    local res = as_dir(r.GetResourcePath())
    local text = M.read_file(res .. "reaper-kb.ini") or ""
    local with_keys = {}
    for id in ("\n" .. text):gmatch("\nKEY%s+%d+%s+%d+%s+_(%S+)") do with_keys[id] = true end
    local out = {}
    for line in text:gmatch("[^\r\n]+") do
        local id, path = line:match('^SCR%s+%d+%s+%d+%s+(%S+)%s+".-"%s+(.+)$')
        if path then
            path = win(trim(path:gsub('^"(.*)"$', "%1")) or "")
            if not path:match("^%a:\\") and not path:match("^\\\\") then path = res .. "Scripts\\" .. path end  -- Scripts からの相対
            out[#out + 1] = {path = path, name = (path:match("[^\\]+$") or ""):lower(), id = id, keys = with_keys[id] or false}
        end
    end
    return out
end

-- 開発中のフォルダ（git の中）か
function M.in_git(path)
    local dir = parent_dir(path)
    while dir ~= "" and dir:match("\\$") do
        if r.file_exists(dir .. ".git\\HEAD") or r.file_exists(dir .. ".git") then return true end
        local up = parent_dir(dir)
        if up == dir then break end
        dir = up
    end
    return false
end

-- アクションごとの登録（reaper-kb.ini）。1 つのアクションに、登録が 1 つだけになるようにまとめるため。
-- {path, id, keys, action, new_path, kind, dev}。kind = "current"（新しいファイルの登録）/ "old"（前の名前・別の場所・名前の変わったもの）。
-- dev：開発中のフォルダ（git の中）なので触らない
function M.old_macros(root)
    local by_name, by_path = {}, {}
    for _, a in ipairs(M.actions(root)) do
        for n in pairs(a.names) do by_name[n] = by_name[n] or a end
        by_path[(root .. win(a.path)):lower()] = a
    end
    local out, seen = {}, {}
    local base = root:lower()
    for _, s_ in ipairs(M.kb_scripts()) do
        local low = s_.path:lower()
        local a = by_path[low] or by_name[s_.name]
        if a and not seen[s_.id] then
            seen[s_.id] = true
            local new_path = root .. win(a.path)
            local kind = low == new_path:lower() and "current" or "old"
            local dev = kind == "old" and low:sub(1, #base) ~= base and M.in_git(s_.path) or false
            out[#out + 1] = {path = s_.path, id = s_.id, keys = s_.keys, action = a, new_path = new_path, kind = kind, dev = dev}
        end
    end
    return out
end

-- 前のマクロのとなりにある「引き継ぐファイル」を、新しいほうへ写す（1つの元につき1回だけ。user\taken_over.txt に覚える）。写した元の一覧
function M.take_over(root, olds)
    local record = root .. "user\\taken_over.txt"
    local done = {}
    for line in (M.read_file(record) or ""):gmatch("[^\r\n]+") do done[line:lower()] = true end
    local copied = {}
    for _, o in ipairs(olds) do
        local from_dir, to_dir = parent_dir(o.path), parent_dir(o.new_path)
        for _, name in ipairs(from_dir:lower() == to_dir:lower() and {} or o.action.takeover) do
            local src = from_dir .. win(name)
            local data = not done[src:lower()] and M.read_file(src)
            if data and #data > 0 and M.write_file(to_dir .. win(name), data) then
                done[src:lower()] = true
                copied[#copied + 1] = src
                r.RecursiveCreateDirectory(root .. "user", 0)
                local f = io.open(record, "ab")
                if f then f:write(src, "\r\n"); f:close() end
            end
        end
    end
    return copied
end

-- 配っているファイル（MANIFEST.txt）の集合（小文字の、入れた場所からのパス）
local function shipped_set(root)
    local set = {}
    local list = M.parse_manifest(M.read_file(root .. "MANIFEST.txt") or "")
    for _, e in ipairs(list or {}) do set[win(e.path):lower()] = true end
    return set
end

-- REAPER を閉じたときにすること（PowerShell に渡す表）。アクションごとに：
--   ・登録が 2 つ以上あれば 1 つにする。残すのはショートカットのある前の登録 → ショートカットのある新しい登録 → 前の登録 → 新しい登録の順。
--     残すのが前の登録なら、新しいファイルに向け直す（repoint）。ほかの登録は消して、ショートカット・ツールバーを残すほうへ寄せる（remap）
--   ・前のマクロ（入れた場所の外）と、そのとなりの「一緒に動かすファイル」を old_macros へ
--   ・入れた場所の中の、配らなくなったファイル（名前の変わった前のマクロ、そのとなりのファイル）も old_macros へ
function M.replace_plan(root, entries, new_ids, stamp)
    local lines, moved = {}, {}
    local backup = root .. "old_macros\\" .. stamp .. "\\"
    local shipped = shipped_set(root)
    local base = root:lower()
    local function move(src, folder, name)
        local low = src:lower()
        if moved[low] then return end
        if low:sub(1, #base) == base and shipped[low:sub(#base + 1)] then return end   -- 配っているファイルは動かさない
        if not r.file_exists(src) then return end
        moved[low] = true
        lines[#lines + 1] = "move\t" .. src .. "\t" .. backup .. folder .. "\\" .. name
    end
    local groups, order_ = {}, {}
    for _, e in ipairs(entries) do
        if not e.dev then
            local g = groups[e.new_path]
            if not g then g = {action = e.action, list = {}} groups[e.new_path] = g order_[#order_ + 1] = e.new_path end
            g.list[#g.list + 1] = e
        end
    end
    for _, new_path in ipairs(order_) do
        local g = groups[new_path]
        local list = g.list
        local nid = new_ids[new_path]
        if nid then
            local have = false
            for _, e in ipairs(list) do if e.id == nid then have = true end end
            if not have then list[#list + 1] = {id = nid, kind = "current", keys = false, new_path = new_path, action = g.action} end
        end
        local olds = 0
        for _, e in ipairs(list) do if e.kind == "old" then olds = olds + 1 end end
        if olds > 0 or #list > 1 then
            local keeper
            for _, rule in ipairs({{"old", true}, {"current", true}, {"old", false}, {"current", false}}) do
                for _, e in ipairs(list) do
                    if not keeper and e.kind == rule[1] and (not rule[2] or e.keys) then keeper = e end
                end
            end
            if keeper.kind == "old" then lines[#lines + 1] = "repoint\t" .. keeper.id .. "\t" .. new_path end
            for _, e in ipairs(list) do
                if e.id ~= keeper.id then lines[#lines + 1] = "remap\t" .. e.id .. "\t" .. keeper.id end
            end
        end
        -- 前のマクロと、そのとなりのファイル
        for _, e in ipairs(list) do
            if e.kind == "old" then
                local dir = parent_dir(e.path)
                local folder = dir:match("([^\\]+)\\$") or "old"
                move(e.path, folder, e.path:match("[^\\]+$"))
                for _, c in ipairs(g.action.companions) do move(dir .. win(c), folder, win(c)) end
            end
        end
    end
    -- 入れた場所の中の、配らなくなった「一緒に動かすファイル」（前の版で配っていたプリセットなど）
    for _, a in ipairs(M.actions(root)) do
        local dir = parent_dir(root .. win(a.path))
        local folder = dir:match("([^\\]+)\\$") or "old"
        for _, c in ipairs(a.companions) do move(dir .. win(c), folder, win(c)) end
        for n in pairs(a.names) do
            if n ~= a.path:match("[^/]+$"):lower() then move(dir .. n, folder, n) end   -- 名前の変わった前のマクロ
        end
    end
    return lines, backup
end

-- 閉じたときに動く PowerShell（ASCII だけで書く。Windows PowerShell 5.1 が BOM の無いファイルを ANSI で読むため）
M.PS1 = [==[
param([string]$Data, [string]$Exe)
$key = [BitConverter]::ToString((New-Object System.Security.Cryptography.SHA1Managed).ComputeHash([Text.Encoding]::UTF8.GetBytes($Data.ToLowerInvariant()))).Replace('-', '')
$mutex = New-Object System.Threading.Mutex($false, 'Local\XLabenToolsReplaceOnExit_' + $key)
if (-not $mutex.WaitOne(0)) { exit 0 }
$utf8 = New-Object System.Text.UTF8Encoding($false)
while ($true) {
  $alive = @(Get-Process -Name reaper -ErrorAction SilentlyContinue | Where-Object { (-not $_.Path) -or ($_.Path -ieq $Exe) })
  if ($alive.Count -eq 0) { break }
  Start-Sleep -Milliseconds 500
}
Start-Sleep -Milliseconds 800
if (-not (Test-Path -LiteralPath $Data)) { exit 0 }
$cfg = @{}; $repoint = @(); $remap = @(); $moves = @()
foreach ($l in [IO.File]::ReadAllLines($Data, $utf8)) {
  $f = $l -split "`t"
  if ($f[0] -eq 'repoint') { $repoint += ,@($f[1], $f[2]) }
  elseif ($f[0] -eq 'remap') { $remap += ,@($f[1], $f[2]) }
  elseif ($f[0] -eq 'move') { $moves += ,@($f[1], $f[2]) }
  elseif ($f.Count -ge 2) { $cfg[$f[0]] = $f[1] }
}
function Log($m) { try { [IO.File]::AppendAllText($cfg['log'], (Get-Date -Format 'yyyy-MM-dd HH:mm:ss ') + $m + "`r`n", $utf8) } catch {} }
function Edit-File($path, $fn) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $text = [IO.File]::ReadAllText($path, $utf8)
  $nl = "`n"; if ($text.Contains("`r`n")) { $nl = "`r`n" }
  $out = & $fn ($text -split "`r?`n")
  [IO.File]::WriteAllText($path, ($out -join $nl), $utf8)
}
try {
  New-Item -ItemType Directory -Force -Path $cfg['backup'] | Out-Null
  foreach ($k in 'kb', 'menu') { if ($cfg[$k] -and (Test-Path -LiteralPath $cfg[$k])) { Copy-Item -LiteralPath $cfg[$k] -Destination (Join-Path $cfg['backup'] ([IO.Path]::GetFileName($cfg[$k]) + '.bak')) -Force } }
  Edit-File $cfg['kb'] {
    param($lines)
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
      $t = $line -split ' ', 5
      $drop = $false
      if ($t.Count -ge 5 -and $t[0] -eq 'SCR') {
        foreach ($p in $repoint) { if ($t[3] -eq $p[0]) { $q = $p[1]; if ($q.Contains(' ')) { $q = '"' + $q + '"' }; $line = 'SCR ' + $t[1] + ' ' + $t[2] + ' ' + $t[3] + ' "Custom: ' + [IO.Path]::GetFileName($p[1]) + '" ' + $q } }
        foreach ($m in $remap) { if ($t[3] -eq $m[0]) { $drop = $true } }
      }
      if (-not $drop) { foreach ($m in $remap) { $line = $line.Replace('_' + $m[0], '_' + $m[1]) }; $out.Add($line) }
    }
    ,$out
  }
  Edit-File $cfg['menu'] { param($lines) $out = New-Object System.Collections.Generic.List[string]; foreach ($line in $lines) { foreach ($m in $remap) { $line = $line.Replace('_' + $m[0], '_' + $m[1]) }; $out.Add($line) }; ,$out }
  Log ('replaced on exit: ' + $repoint.Count + ' registrations')
  foreach ($mv in $moves) {
    if (Test-Path -LiteralPath $mv[0]) {
      try { New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($mv[1])) | Out-Null; Move-Item -LiteralPath $mv[0] -Destination $mv[1] -Force; Log ('moved ' + $mv[0]) } catch { Log ('could not move ' + $mv[0] + ': ' + $_) }
    }
  }
  Remove-Item -LiteralPath $Data -Force
} catch { Log ('replace on exit failed: ' + $_) }
]==]

-- 閉じたときにすることを書いて、待つ PowerShell を動かす（もう待っているものがあれば、表を書き直すだけ。待つのは1つ）
function M.schedule(root, lines, backup)
    local res = as_dir(r.GetResourcePath())
    local data = root .. "user\\replace_on_exit.tsv"
    local ps1 = root .. "user\\replace_on_exit.ps1"
    local head = {"kb\t" .. res .. "reaper-kb.ini", "menu\t" .. res .. "reaper-menu.ini", "backup\t" .. backup,
                  "log\t" .. root .. "user\\update_log.txt"}
    for _, l in ipairs(lines) do head[#head + 1] = l end
    if not (M.write_file(data, table.concat(head, "\r\n") .. "\r\n") and M.write_file(ps1, M.PS1)) then return false end
    local exe = as_dir(r.GetExePath()) .. "reaper.exe"
    local cmd = string.format('"%s" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%s" -Data "%s" -Exe "%s"',
        (os.getenv("SystemRoot") or "C:\\Windows") .. "\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", ps1, data, exe)
    r.ExecProcess(cmd, -1)  -- 待たない
    return true
end

-- 前のマクロを置き換え、登録を 1 つにまとめる準備をする。
-- {replaced = 置き換える前の登録, merged = まとめて消す新しい登録の数, moves = 動かすファイルの数, dev = 触らないもの, copied = 引き継いだファイル}
function M.replace_old(root, new_ids)
    local all = M.old_macros(root)
    local olds, dev = {}, {}
    for _, o in ipairs(all) do
        if o.dev then dev[#dev + 1] = o elseif o.kind == "old" then olds[#olds + 1] = o end
    end
    local out = {replaced = olds, dev = dev, copied = {}, merged = 0, moves = 0}
    out.copied = M.take_over(root, olds)
    local lines, backup = M.replace_plan(root, all, new_ids or {}, os.date("%Y%m%d"))
    for _, l in ipairs(lines) do
        local k = l:match("^(%a+)")
        if k == "move" then out.moves = out.moves + 1 elseif k == "remap" then out.merged = out.merged + 1 end
    end
    out.backup = backup
    if #lines > 0 then out.scheduled = M.schedule(root, lines, backup) end
    out.pending = #lines > 0
    return out
end

------------------------------------------------------------------------------ データの ZIP
-- XLabenTools…zip を探す（dirs の順に）。見つからなければ nil
function M.find_zip(dirs)
    for _, dir in ipairs(dirs) do
        if dir ~= "" then
            local d = dir:gsub("\\$", "")
            r.EnumerateFiles(d, -1)
            local i = 0
            while true do
                local name = r.EnumerateFiles(d, i)
                if not name then break end
                if name:lower():match("^xlabentools.*%.zip$") then return as_dir(d) .. name end
                i = i + 1
            end
        end
    end
    return nil
end

-- データの ZIP から入れる：Python・モデル・MIDIChecker を入れ替える。うまくいったら ZIP を消す。
-- 前の全部入りの ZIP（コードも入っている）なら、コードは ZIP のほうが新しいときだけ入れる。
-- 成功：{to, code = コードも入れたか, removed_zip}。だめ：nil, 理由（コードと Python などは前のまま）
function M.install_zip(root, zip)
    r.RecursiveCreateDirectory(root, 0)
    local tmp = root .. "_zip"
    M.rmdir(tmp)
    local src, err = M.extract(zip, tmp, 0)  -- 0：終わるまで待つ（数分）
    if not src then M.rmdir(tmp) return nil, "ZIP を展開できませんでした（空きが足りているかを確かめてください）。\n" .. tostring(err) end
    if not r.file_exists(src .. "A2M\\BASE.txt") then
        M.rmdir(tmp)
        return nil, "X-Laben ツールのデータの ZIP ではありません：" .. zip
    end
    local have, zipver = trim(M.read_file(root .. "VERSION.txt")), trim(M.read_file(src .. "VERSION.txt"))
    local out = {to = zipver, code = false, base = trim(M.read_file(src .. "A2M\\BASE.txt"))}
    if r.file_exists(src .. "MANIFEST.txt") and (not M.is_install(root) or M.version_gt(zipver, have)) then
        local res, why = M.apply_source(root, src)
        if not res then M.rmdir(tmp) return nil, why end
        out.code = true
    end
    -- 前のものを全部どけてから、新しいものを入れる（どけられなければ、どけたものを戻して止める）
    local parked = {}
    for i, sub in ipairs(M.BASE_DIRS) do
        if M.dir_has_content(src .. sub) and M.dir_has_content(root .. sub) then
            local away = tmp .. "\\old_" .. i
            if not os.rename(root .. sub, away) then
                for _, p in ipairs(parked) do os.rename(p[2], p[1]) end
                M.rmdir(tmp)
                return nil, "使用中のファイルがあって入れ替えられませんでした。AI の下書き・納品チェックを止めて、もう一度実行してください。\n" .. root .. sub
            end
            parked[#parked + 1] = {root .. sub, away}
        end
    end
    for _, sub in ipairs(M.BASE_DIRS) do
        if M.dir_has_content(src .. sub) then
            r.RecursiveCreateDirectory(parent_dir(root .. sub), 0)
            if not os.rename(src .. sub, root .. sub) then
                M.rmdir(tmp)
                return nil, "入れられませんでした：" .. root .. sub
            end
        end
    end
    M.write_file(root .. "A2M\\BASE.txt", M.read_file(src .. "A2M\\BASE.txt"))
    M.rmdir(tmp)
    out.removed_zip = os.remove(zip) and true or false
    return out
end

------------------------------------------------------------------------------ 知らせること
-- AI の下書きの土台（Python・モデル・MIDIChecker。データの ZIP から入る）：("ok" / "none" / "old" / "unused", 知らせる文)
function M.base_state(root)
    local need = trim(M.read_file(root .. "A2M\\BASE_REQUIRED.txt"))
    if not need then return "unused", nil end
    local have = trim(M.read_file(root .. "A2M\\BASE.txt"))
    local how = "データの ZIP（XLabenTools_data_….zip）を、入れた場所の XLabenTools フォルダにそのまま置いて（「フォルダを開く」で開ける）、\n" ..
        "もう一度「XLaben_Update」を実行してください。自動で入って、ZIP は消えます。"
    if not have then
        return "none", "AI の下書きと納品チェックを使うには、Python・モデル・MIDIChecker が要ります。\n" .. how ..
            "\nミキサー・EFX・ドラム・再生・オートストラマーは、このままで使えます。"
    end
    if have ~= need then
        return "old", "AI の下書きの土台（Python・モデル・MIDIChecker）が古いです。新しいデータの ZIP が要ります。\n" .. how
    end
    return "ok", nil
end

function M.base_note(root)
    local _, note = M.base_state(root)
    return note
end

-- CHANGES.txt を版ごとに分ける：{{version = "2026.11.16", lines = {…}}, …}（新しい順に書いてある。「■ 版」が見出し）
function M.sections(text)
    local out = {}
    for line in (text or ""):gsub("^\239\187\191", ""):gmatch("[^\r\n]+") do
        local v = line:match("^■%s*(%S+)")
        if v then
            out[#out + 1] = {version = v, lines = {}}
        elseif #out > 0 then
            local s = out[#out]
            s.lines[#s.lines + 1] = line
        end
    end
    return out
end

-- from の版より新しい分の変更点（from が nil なら、いちばん新しい版だけ）。
-- (変更点の文, やることの行の一覧)。「★」で始まる行は、更新した人がやること（設定など）
function M.news(root, from)
    local text, todo = {}, {}
    for i, s in ipairs(M.sections(M.read_file(root .. "CHANGES.txt"))) do
        if s.version == from or (not from and i > 1) then break end
        text[#text + 1] = "■ " .. s.version
        for _, line in ipairs(s.lines) do
            text[#text + 1] = line
            local t = line:match("^%s*★%s*(.+)$")
            if t and from then todo[#todo + 1] = "・" .. t .. "（" .. s.version .. "）" end
        end
    end
    return table.concat(text, "\n"), todo
end

local function log(root, fmt, ...)
    local f = io.open(root .. "user\\update_log.txt", "ab")
    if not f then
        r.RecursiveCreateDirectory(root .. "user", 0)
        f = io.open(root .. "user\\update_log.txt", "ab")
    end
    if f then
        f:write(os.date("%Y-%m-%d %H:%M:%S "), string.format(fmt, ...), "\r\n")
        f:close()
    end
end
M.log = log

local FIRST_TIME = "■ 初めての人がやること\n" ..
    "・アクションリストで「XLaben」を探して、ショートカットを割り当てる（再生・停止の XLaben_Play.lua は Space に）。\n" ..
    "　前のマクロ（EFXUI・ドラム・ミキサーなど）に割り当てていたショートカットは、REAPER を開き直すとそのまま新しいほうで動きます。\n" ..
    "・REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。\n" ..
    "・使い方は、入れた場所の README.txt（「フォルダを開く」で開ける）。"

-- 置き換えの知らせ
function M.replace_text(rep)
    if not rep.pending then return nil end
    local lines = {"■ 前のマクロの片付け（REAPER を閉じたときに行う）"}
    if #rep.replaced > 0 then
        lines[#lines + 1] = "次の前のマクロを、新しいものに置き換えます。ショートカットとツールバーのボタンは、そのまま新しいほうで動きます："
        for _, o in ipairs(rep.replaced) do lines[#lines + 1] = "・" .. o.path .. " → " .. o.action.label end
    end
    if rep.merged > 0 then lines[#lines + 1] = string.format("同じマクロの登録が重なっていたので、1 つにまとめます（%d 個を消す。ショートカットは残すほうへ）。", rep.merged) end
    if rep.moves > 0 then lines[#lines + 1] = string.format("要らなくなったファイル %d 個を %s に移します（消しません）。", rep.moves, rep.backup or "") end
    for _, c in ipairs(rep.copied) do lines[#lines + 1] = "引き継いだ設定・プリセット：" .. c end
    if not rep.scheduled then lines[#lines + 1] = "（片付けの準備ができませんでした。user フォルダに書けるかを確かめてください）" end
    lines[#lines + 1] = "→ REAPER を閉じて、開き直してください。"
    return table.concat(lines, "\n")
end

------------------------------------------------------------------------------ 入れる・更新する
-- 結果の表を返す：status = "installed" / "updated" / "latest" / "offline" / "failed"、text = 見せる文
function M.run(root)
    root = root or M.resolve_root()
    local res = {root = root, from = trim(M.read_file(root .. "VERSION.txt"))}
    local before = {}
    for _, a in ipairs(M.actions(root)) do before[a.path] = true end
    local function finish(status, parts)
        res.status = status
        local done, missing, ids = {}, {}, {}
        if status ~= "failed" then done, missing, ids = M.register(root) end
        res.registered, res.missing = #done, missing
        if #missing > 0 then parts[#parts + 1] = "見つからなかったスクリプト：\n" .. table.concat(missing, "\n") end
        if status ~= "failed" and M.is_install(root) then
            M.set_root(root)
            res.replace = M.replace_old(root, ids)
            local t = M.replace_text(res.replace)
            if t then parts[#parts + 1] = t end
            res.base_note = M.base_note(root)
            if res.base_note then parts[#parts + 1] = "■ AI の下書き・納品チェック\n" .. res.base_note end
        end
        if status == "installed" then parts[#parts + 1] = FIRST_TIME end
        res.text = table.concat(parts, "\n\n")
        log(root, "%s %s -> %s %s", status, res.from or "-", res.to or "-", res.error or "")
        return res
    end
    if root == "" or M.SOURCE.repo == "" then
        res.error = "配る元が書かれていません"
        return finish("failed", {"このスクリプトには配る元（GitHub）が書かれていません。配られた XLaben_Update.lua を使ってください。"})
    end
    -- 1. 新しい版があるか
    local latest, err = M.latest_version(root)
    if not latest then
        res.error = err
        if res.from then
            return finish("offline", {string.format("更新を確かめられませんでした（ネットにつながっていない、または配る元が見えません）。\n今の版（%s）のまま使えます。\n\n%s",
                res.from, res.error)})
        end
        return finish("failed", {"ダウンロードできませんでした。ネットにつながっているかを確かめて、もう一度押してください。\n" ..
            "（配る元が非公開のときは、入れる場所の user\\github_token.txt が要ります）\n\n" .. res.error})
    end
    res.to = latest
    if latest == res.from then return finish("latest", {"最新です（版 " .. latest .. "）。"}) end
    -- 2. 取ってきて、入れ替える（中身が一覧と違えば、何も書き換えない）
    local tmp = M.tmp_dir()
    M.rmdir(tmp)
    r.RecursiveCreateDirectory(tmp, 0)
    local src, ok, applied, untouched
    ok, err = M.curl(M.zip_url(), tmp .. "\\pkg.zip", nil, root)
    if ok then src, err = M.extract(tmp .. "\\pkg.zip", tmp .. "\\x") end
    if src then applied, err, untouched = M.apply_source(root, src) end
    M.rmdir(tmp)
    if not applied then
        res.error = err
        if src and untouched == false then
            return finish("failed", {"書けないファイルがありました。開いているツール（ミキサー・EFX・ドラム）と AI の下書きを止めて、もう一度押してください。\n\n" ..
                tostring(err)})
        end
        return finish("failed", {"更新できませんでした（何も書き換えていません。今の版のまま使えます）。\n\n" .. tostring(err)})
    end
    res.to = applied.to or latest
    res.copied, res.removed = applied.copied, applied.removed
    local parts = {}
    local head = res.from and string.format("更新しました：%s → %s", res.from, res.to) or ("入れました（版 " .. res.to .. "）")
    parts[#parts + 1] = head .. string.format("\n場所：%s\n（%d ファイルを入れ替え、%d ファイルを消しました）", root, res.copied, res.removed)
    local changes, todo = M.news(root, res.from)
    -- 増えたアクション（更新のときだけ。初めてのときは、下の「初めての人がやること」に書いてある）
    res.new_actions = {}
    if res.from then
        for _, a in ipairs(M.actions(root)) do
            if not before[a.path] then res.new_actions[#res.new_actions + 1] = string.format("・%s（%s）", a.label, a.path:match("[^/]+$")) end
        end
    end
    res.todo = todo
    if #todo > 0 or #res.new_actions > 0 then
        local lines = {"■ やること"}
        for _, t in ipairs(todo) do lines[#lines + 1] = t end
        if #res.new_actions > 0 then
            lines[#lines + 1] = "・新しいアクションが増えました。アクションリストで探して、ショートカットを割り当ててください："
            for _, a in ipairs(res.new_actions) do lines[#lines + 1] = "　" .. a end
        end
        parts[#parts + 1] = table.concat(lines, "\n")
    end
    if res.from then parts[#parts + 1] = "REAPER を一度閉じて開き直してください（開いていたツールは前の版のまま動いているため）。" end
    if changes ~= "" then parts[#parts + 1] = "変更点\n" .. changes end
    return finish(res.from and "updated" or "installed", parts)
end

-- データの ZIP から入れてから、GitHub の新しい版を確かめる（まだ入れていなければ、コードは GitHub から入る）
function M.run_zip(root, zip)
    local z, err = M.install_zip(root, zip)
    if not z then
        log(root, "zip failed %s", tostring(err))
        return {status = "failed", text = "データの ZIP から入れられませんでした（ZIP は残してあります）。\n\n" .. tostring(err), root = root}
    end
    log(root, "zip %s code=%s", tostring(z.to), tostring(z.code))
    local res = M.run(root)
    local head = "データの ZIP から、Python・モデル・MIDIChecker を入れました。" ..
        (z.removed_zip and "ZIP は消しました。" or "ZIP を消せませんでした（手で消してください）。")
    res.zip = z
    res.text = head .. "\n\n" .. res.text
    return res
end

------------------------------------------------------------------------------ 場所を変える
-- 選んだフォルダから、入れる場所を決める：選んだフォルダの中に XLabenTools を作る（選んだフォルダが XLabenTools ならそこ）
function M.root_in(picked)
    local dir = as_dir(picked)
    if dir == "" then return "" end
    if dir:lower():match("\\" .. M.FOLDER:lower() .. "\\$") then return dir end
    return dir .. M.FOLDER .. "\\"
end

-- old_root を new_root へ移す。(true, 文) か (false, 理由)。まだ入れていなければ、入れる場所を変えるだけ
function M.move(old_root, new_root)
    if new_root == "" then return false, "場所が空です" end
    if new_root:lower() == old_root:lower() then return false, "今と同じ場所です" end
    if new_root:lower():sub(1, #old_root) == old_root:lower() then return false, "今のフォルダの中には移せません" end
    if not M.is_install(old_root) then
        M.set_root(new_root)
        return true, "入れる場所を変えました：\n" .. new_root
    end
    if M.dir_has_content(new_root) then return false, "移す先のフォルダが空ではありません：\n" .. new_root end
    local from, to = old_root:gsub("\\$", ""), new_root:gsub("\\$", "")
    r.RecursiveCreateDirectory(to:match("^(.*)\\[^\\]+$") or to, 0)
    M.rmdir(to)  -- 空のフォルダがあれば消す（名前を変えて移すため。空なのは上で確かめた）
    local moved = os.rename(from, to)
    if not moved then
        if from:sub(1, 2):lower() == to:sub(1, 2):lower() then
            return false, "移せませんでした。開いているツール（ミキサー・EFX・ドラム）と AI の下書きを止めて、もう一度試してください。"
        end
        -- 別のドライブ：全部写してから、前のフォルダを消す（写すのに失敗したら、前のまま）
        local code = M.exec(string.format('"%s" "%s" "%s" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1', sysexe("robocopy.exe"), from, to), 0)
        if code < 0 or code >= 8 or not M.is_install(new_root) then
            M.rmdir(to)
            return false, "写せませんでした（前の場所のまま使えます）。空きが足りているか、開いているツールが無いかを確かめてください。"
        end
        M.rmdir(from)
    end
    M.set_root(new_root)
    -- 新しい場所で登録し、前の場所の登録（ショートカット付き）は、REAPER を閉じたときに新しい場所へ向け直す
    local _, _, ids = M.register(new_root)
    local rep = M.replace_old(new_root, ids)
    log(new_root, "moved %s -> %s", old_root, new_root)
    return true, "移しました：\n" .. new_root ..
        "\n\nREAPER を閉じて開き直すと、ショートカットとツールバーのボタンも、そのまま新しい場所のマクロで動きます" ..
        (rep.scheduled and "。" or "（準備ができませんでした。ショートカットを割り当て直してください）。") ..
        "\n開き直すまでは、アクションリストから実行してください。"
end

-- フォルダを選んでもらう。選ばなければ nil。（js_ReaScriptAPI があれば選ぶ画面、無ければパスを入力）
function M.pick_folder(start)
    if r.JS_Dialog_BrowseForFolder then
        local ok, folder = r.JS_Dialog_BrowseForFolder("XLabenTools フォルダを作る場所", start or "")
        if ok == 1 and folder and folder ~= "" then return folder end
        return nil
    end
    local ok, text = r.GetUserInputs("入れる場所（この中に XLabenTools フォルダを作る）", 1, "フォルダのパス,extrawidth=420,separator=\n", start or "")
    if ok and trim(text) then return trim(text) end
    return nil
end

if XL_UPDATE_NO_RUN then return M end

------------------------------------------------------------------------------ 画面（ミキサーと同じ色）
local function hex(s)
    return tonumber(s:sub(2, 3), 16) / 255, tonumber(s:sub(4, 5), 16) / 255, tonumber(s:sub(6, 7), 16) / 255
end
local C = {
    bg = {hex("#14161b")}, panel = {hex("#1c1f26")}, button = {hex("#232730")}, line = {hex("#2e333d")},
    text = {hex("#e7e9ee")}, dim = {hex("#8d93a3")}, accent = {hex("#4ea1ff")}, warn = {hex("#ffad33")}, ok = {hex("#3dd17f")},
}
local W, H, PAD, ROW = 760, 600, 16, 24
local ui = {root = M.resolve_root(), message = "", scroll = 0, pending = "check", down = false}

local function color(c, a) gfx.set(c[1], c[2], c[3], a or 1) end

local function text_at(s, x, y, c, font)
    gfx.setfont(font or 1)
    color(c or C.text)
    gfx.x, gfx.y = x, y
    gfx.drawstr(s)
end

-- 幅に収まるように折り返す（UTF-8 の文字の途中では切らない）
local wrap_cache = {}
local function wrap(text, width)
    local key = width .. "\0" .. text
    if wrap_cache.key == key then return wrap_cache.lines end
    gfx.setfont(1)
    local lines = {}
    for line in (text .. "\n"):gmatch("(.-)\n") do
        local cur = ""
        for ch in line:gmatch("[\1-\127\194-\244][\128-\191]*") do
            if cur ~= "" and gfx.measurestr(cur .. ch) > width then
                lines[#lines + 1] = cur
                cur = ch
            else
                cur = cur .. ch
            end
        end
        lines[#lines + 1] = cur
    end
    wrap_cache.key, wrap_cache.lines = key, lines
    return lines
end

-- ボタン。押されたら true
local function button(label, x, y, w, h, enabled, strong)
    local hot = enabled and gfx.mouse_x >= x and gfx.mouse_x < x + w and gfx.mouse_y >= y and gfx.mouse_y < y + h
    color(strong and enabled and C.accent or C.button, (hot and 1) or (enabled and 0.85) or 0.4)
    gfx.rect(x, y, w, h, 1)
    color(C.line)
    gfx.rect(x, y, w, h, 0)
    gfx.setfont(2)
    local tw, th = gfx.measurestr(label)
    text_at(label, x + (w - tw) / 2, y + (h - th) / 2, (strong and enabled) and C.bg or (enabled and C.text or C.dim), 2)
    return hot and ui.clicked
end

local function refresh()
    ui.have = trim(M.read_file(ui.root .. "VERSION.txt"))
    ui.base = M.base_state(ui.root)
    ui.zip = M.find_zip({ui.root, M.HERE ~= ui.root and M.HERE or ""})
    ui.olds = 0
    if M.is_install(ui.root) then
        for _, o in ipairs(M.old_macros(ui.root)) do if not o.dev and o.kind == "old" then ui.olds = ui.olds + 1 end end
    end
end

local function do_pending()
    local what = ui.pending
    ui.pending = nil
    if what == "check" then
        refresh()
        -- 入れてあれば、登録・前のマクロ・要らないファイルの片付けを、ここでも準備する（REAPER を閉じたときに行う）
        if M.is_install(ui.root) then
            local _, _, ids = M.register(ui.root)
            local rep = M.replace_old(ui.root, ids)
            local t = M.replace_text(rep)
            if t then ui.message = t end
        end
        ui.latest, ui.latest_err = M.latest_version(ui.root)
        -- 入れた場所にデータの ZIP が置いてあれば、そのまま入れる
        if ui.zip and M.is_install(ui.root) then ui.pending = "zip" end
    elseif what == "run" or what == "zip" then
        local res = (ui.zip and (what == "zip" or not M.is_install(ui.root))) and M.run_zip(ui.root, ui.zip) or M.run(ui.root)
        ui.message, ui.scroll = res.text, 0
        if res.to then ui.latest, ui.latest_err = res.to, nil end
        refresh()
    elseif what == "move" then
        local picked = M.pick_folder(ui.root)
        if picked then
            local new_root = M.root_in(picked)
            local installed = M.is_install(ui.root)
            local ask = installed and ("フォルダごと、次の場所へ移します。\n\n" .. new_root ..
                "\n\nショートカットは、REAPER を開き直すとそのまま新しい場所で動きます。\n" ..
                "（AI の下書きの Python とモデルも一緒に移します。別のドライブへは数分かかります）") or ("次の場所に入れます。\n\n" .. new_root)
            if r.ShowMessageBox(ask, M.TITLE, 1) == 1 then
                local ok, msg = M.move(ui.root, new_root)
                ui.message, ui.scroll = msg, 0
                if ok then ui.root = new_root end
                refresh()
            end
        end
    elseif what == "open" then
        r.RecursiveCreateDirectory(ui.root, 0)
        r.ExecProcess(string.format('"%s" "%s"', (os.getenv("SystemRoot") or "C:\\Windows") .. "\\explorer.exe", (ui.root:gsub("\\$", ""))), -1)
    end
end

local BUSY = {check = "新しい版があるかを確かめています…", run = "ダウンロードして入れ替えています…（少し待つ）",
              zip = "データの ZIP を展開して入れています…（数分。その間 REAPER は止まって見えます）",
              move = "場所を変えています…（Python とモデルがあると数分かかる）"}

local function frame()
    local ch = gfx.getchar()
    if ch < 0 or ch == 27 then return end
    local down = gfx.mouse_cap & 1 == 1
    ui.clicked = ui.down and not down  -- 離したときに押したことにする
    ui.down = down
    color(C.bg)
    gfx.rect(0, 0, gfx.w, gfx.h, 1)
    local x, y, w = PAD, PAD, gfx.w - PAD * 2
    text_at("X-Laben ツール", x, y, C.text, 3)
    y = y + 34
    local installed = M.is_install(ui.root)
    local label
    if not installed then label = ui.zip and "ZIP からインストールする" or "インストールする"
    elseif ui.latest and ui.latest ~= ui.have then label = "更新する（" .. ui.latest .. "）"
    else label = "更新を確かめる" end
    -- 版
    text_at("今の版", x, y, C.dim)
    text_at(installed and (ui.have or "？") or "まだ入れていません", x + 110, y, C.text)
    y = y + ROW
    text_at("配っている版", x, y, C.dim)
    if ui.latest then
        text_at(ui.latest .. ((installed and ui.latest == ui.have) and "　（最新です）" or ""), x + 110, y, (installed and ui.latest == ui.have) and C.ok or C.accent)
    else
        text_at(ui.latest_err and "確かめられません（ネットにつながっていない？）" or "…", x + 110, y, C.warn)
    end
    y = y + ROW
    -- 場所
    text_at("入れる場所", x, y, C.dim)
    for _, line in ipairs(wrap(ui.root, w - 110)) do
        text_at(line, x + 110, y, C.text)
        y = y + ROW - 4
    end
    y = y + 4
    if not M.ascii_path(ui.root) then
        text_at("この場所は日本語などを含むので、AI の下書きは動きません（「場所を変える」で英数字だけの場所へ）", x + 110, y, C.warn)
        y = y + ROW
    end
    text_at("AI の下書き", x, y, C.dim)
    local base = {ok = {"使えます（Python・モデル・MIDIChecker あり）", C.ok},
                  none = {"Python・モデルがまだありません（データの ZIP をこのフォルダに置く）", C.dim},
                  old = {"Python・モデルが古い（新しいデータの ZIP をこのフォルダに置く）", C.warn}, unused = {"—", C.dim}}
    local b = base[installed and ui.base or "unused"] or base.unused
    if ui.zip then b = {"データの ZIP を見つけました：" .. (ui.zip:match("[^\\]+$") or ""), C.accent} end
    text_at(b[1], x + 110, y, b[2])
    y = y + ROW + 8
    -- ボタン
    local busy = ui.pending ~= nil
    if button(label, x, y, 250, 36, not busy, true) then ui.pending = "run" end
    if button("場所を変える", x + 262, y, 130, 36, not busy) then ui.pending = "move" end
    if button("フォルダを開く", x + 404, y, 130, 36, not busy) then ui.pending = "open" end
    y = y + 46
    if (ui.olds or 0) > 0 then
        text_at(string.format("前のマクロが %d 個あります。REAPER を閉じると片付けます（ショートカットはそのまま）", ui.olds), x, y, C.warn)
        y = y + ROW
    end
    color(C.line)
    gfx.line(x, y, x + w, y)
    y = y + 10
    -- 知らせ（ホイールで上下）
    local body = busy and BUSY[ui.pending] or ui.message
    if body == "" then body = installed and "「更新を確かめる」を押すと、新しい版があれば入れ替えます。" or "「インストールする」を押すと、上の場所に全部入れて、アクションに登録します。" end
    local lines = wrap(body, w)
    local rows = math.max(1, math.floor((gfx.h - y - PAD) / (ROW - 4)))
    if gfx.mouse_wheel ~= 0 then
        ui.scroll = math.max(0, math.min(math.max(0, #lines - rows), ui.scroll - (gfx.mouse_wheel > 0 and 3 or -3)))
        gfx.mouse_wheel = 0
    end
    for i = 1, rows do
        local line = lines[i + ui.scroll]
        if not line then break end
        text_at(line, x, y, line:match("^■") and C.accent or C.text)
        y = y + ROW - 4
    end
    gfx.update()
    -- 時間のかかることは、「…しています」を描いた次の回にする
    if ui.run_next then
        ui.run_next = false
        do_pending()
    elseif ui.pending then
        ui.run_next = true
    end
    r.defer(frame)
end

gfx.init(M.TITLE, W, H)
gfx.setfont(1, "Meiryo UI", 14)
gfx.setfont(2, "Meiryo UI", 15, string.byte("b"))
gfx.setfont(3, "Meiryo UI", 21, string.byte("b"))
M.ui, M.frame = ui, frame
frame()
return M
