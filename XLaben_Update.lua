-- @description X-Laben ツール：インストール・更新
-- @version 2.0
--
-- これ 1 つで「入れる」「更新する」「入れる場所を変える」をする。実行すると画面が出る。
--   ・初めて：このファイルを REAPER で実行する（アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む）。
--     「インストールする」を押すと、REAPER の Scripts フォルダの中の XLabenTools フォルダに全部入り、アクションに登録される。
--     入れる前に「場所を変える」で別の場所を選べる。
--   ・更新：アクション「XLaben_Update.lua」を実行して「更新する」を押す。新しい版があれば入れ替える。
--   ・場所を変える：入れたあとでも、フォルダごと別の場所へ移せる（アクションは新しい場所で登録し直す。ショートカットは割り当て直し）。
-- 入れ替えるのは、配っているファイルだけ（MANIFEST.txt に書いてあるもの）。次のものには触らない：
--   user フォルダ、ログ、自分で保存した EFX のプリセット、A2M の python・models（全部入りの ZIP にだけ入っている）。
-- 使うもの：Windows に最初から入っている curl.exe と tar.exe（Windows 10 の 1803 以降）。git は要らない。
-- 配る元が非公開のときは、入れる場所の user\github_token.txt に読み取り専用のトークンを1行で置く（表示もログにも書かない）。

local r = reaper
local M = {}

M.SOURCE = {repo = "nemico/xl-tools", branch = "main"}  -- 配る元（GitHub の owner/name）。scripts/make_dist.py が配布のときに書き込む
M.TITLE = "X-Laben ツール：インストール・更新"
M.EXT = "XLabenTools"       -- REAPER に覚えておく名前（入れる場所）
M.FOLDER = "XLabenTools"    -- 専用のフォルダの名前
M.HERE = (((debug.getinfo(1, "S").source:match("^@(.*[\\/])")) or ""):gsub("/", "\\"))  -- このファイルがあるフォルダ
M.TIMEOUT_MS = 320000       -- ダウンロードを待つ上限
-- 前に配っていたマクロ（今のものに置き換わった）。別の場所に登録が残っていたら知らせる（小文字で書く）
M.OLD_NAMES = {"sc_drum_macro.lua", "x-labenmixer.lua"}

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

-- (終了コード, 出力)。REAPER の ExecProcess は「終了コード\n出力」を返す。失敗・時間切れは nil
function M.exec(cmd, timeout_ms)
    local out = r.ExecProcess(cmd, timeout_ms or 120000)
    if not out then return -1, "" end
    local code, rest = out:match("^(%-?%d+)%s*\n?(.*)$")
    return tonumber(code) or -1, rest or ""
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

-- ZIP を dir に展開し、いちばん上のフォルダ（GitHub の ZIP は「owner-name-番号」が1つ）を返す
function M.extract(zip, dir)
    r.RecursiveCreateDirectory(dir, 0)
    local code, out = M.exec(string.format('"%s" -xf "%s" -C "%s"', sysexe("tar.exe"), zip, dir), 120000)
    if code ~= 0 then return nil, string.format("tar %d %s", code, trim(out) or "") end
    r.EnumerateSubdirectories(dir, -1)  -- 前に見た結果を忘れさせる
    local top = r.EnumerateSubdirectories(dir, 0)
    if not top then return nil, "ZIP の中にフォルダがありません" end
    return dir .. "\\" .. top .. "\\"
end

function M.rmdir(dir)
    M.exec(string.format('"%s" /C rmdir /S /Q "%s"', sysexe("cmd.exe"), dir), 600000)
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

------------------------------------------------------------------------------ アクション
-- actions.txt（1行に「パス<TAB>説明」）の一覧：{path = "REAPER/…", label = 説明}
function M.actions(root)
    local out = {}
    for line in (M.read_file(root .. "actions.txt") or ""):gmatch("[^\r\n]+") do
        local path, label = line:match("^([^\t]+)\t(.*)$")
        if path then out[#out + 1] = {path = path, label = label} end
    end
    return out
end

-- actions.txt のスクリプトをアクションに登録する。もう登録してあれば何も変わらない
function M.register(root)
    local done, missing = {}, {}
    for _, a in ipairs(M.actions(root)) do
        local full = root .. win(a.path)
        if r.file_exists(full) then
            r.AddRemoveReaScript(true, 0, full, true)
            done[#done + 1] = string.format("・%s（%s）", a.label, a.path:match("[^/]+$"))
        else
            missing[#missing + 1] = a.path
        end
    end
    return done, missing
end

function M.unregister(paths)
    for _, path in ipairs(paths) do r.AddRemoveReaScript(false, 0, path, true) end
end

-- REAPER に登録してあるスクリプト（reaper-kb.ini の SCR の行）：{path = フルパス, name = 小文字のファイル名}
function M.kb_scripts()
    local res = as_dir(r.GetResourcePath())
    local out = {}
    for line in (M.read_file(res .. "reaper-kb.ini") or ""):gmatch("[^\r\n]+") do
        local path = line:match('^SCR%s+%d+%s+%d+%s+%S+%s+".-"%s+(.+)$')
        if path then
            path = win(trim(path:gsub('^"(.*)"$', "%1")) or "")
            if not path:match("^%a:\\") and not path:match("^\\\\") then path = res .. "Scripts\\" .. path end  -- Scripts からの相対
            out[#out + 1] = {path = path, name = (path:match("[^\\]+$") or ""):lower()}
        end
    end
    return out
end

-- 入れる場所の外に登録が残っている、同じ名前（か、前に配っていた名前）のスクリプト。今動いているこのファイルは入れない
function M.old_registrations(root)
    local names = {}
    for _, n in ipairs(M.OLD_NAMES) do names[n] = true end
    for _, a in ipairs(M.actions(root)) do names[a.path:match("[^/]+$"):lower()] = true end
    local out, seen = {}, {}
    local base, here = root:lower(), (M.HERE .. "XLaben_Update.lua"):lower()
    for _, s in ipairs(M.kb_scripts()) do
        local low = s.path:lower()
        if names[s.name] and low:sub(1, #base) ~= base and low ~= here and not seen[low] then
            seen[low] = true
            out[#out + 1] = s.path
        end
    end
    return out
end

-- 前の EFXUI（別の場所に登録してあるもの）で保存したプリセットを引き継ぐ。引き継いだら元のパス
function M.import_presets(root, old)
    for _, path in ipairs(old) do
        if path:lower():match("xlaben_efx_controller%.lua$") then
            local src = path:gsub("[^\\]+$", "") .. "xlaben_efx_presets.json"
            local data = M.read_file(src)
            if data and #data > 0 and M.write_file(root .. "REAPER\\EFXUI\\xlaben_efx_presets.json", data) then return src end
        end
    end
    return nil
end

------------------------------------------------------------------------------ 知らせること
-- AI の下書きの土台（Python・モデル。全部入りの ZIP にだけ入っている）：("ok" / "none" / "old" / "unused", 知らせる文)
function M.base_state(root)
    local need = trim(M.read_file(root .. "A2M\\BASE_REQUIRED.txt"))
    if not need then return "unused", nil end
    local have = trim(M.read_file(root .. "A2M\\BASE.txt"))
    if not have then
        return "none", "AI の下書きを使うには、全部入りの ZIP（Python とモデル入り）の中身を、入れた場所のフォルダに上書きで展開してください\n" ..
            "（「フォルダを開く」で開ける。ZIP の中の XLabenTools フォルダの中身を、そのフォルダに入れる）。\n" ..
            "ミキサー・EFX・ドラム・再生は、このままで使えます。"
    end
    if have ~= need then
        return "old", "AI の下書きの土台（Python・モデル）が古いです。新しい全部入りの ZIP をもらって、入れた場所のフォルダに上書きで展開し、\n" ..
            "もう一度「更新する」を押してください。ミキサー・EFX・ドラム・再生は、このままで使えます。"
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

local FIRST_TIME = "■ 初めての人がやること\n" ..
    "・アクションリストで「XLaben」を探して、ショートカットを割り当てる（再生・停止の XLaben_Play.lua は Space に）。\n" ..
    "・REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。\n" ..
    "・使い方は、入れた場所の README.txt（「フォルダを開く」で開ける）。"

------------------------------------------------------------------------------ 入れる・更新する
-- 結果の表を返す：status = "installed" / "updated" / "latest" / "offline" / "failed"、text = 見せる文
function M.run(root)
    root = root or M.resolve_root()
    local res = {root = root, from = trim(M.read_file(root .. "VERSION.txt"))}
    local before = {}
    for _, a in ipairs(M.actions(root)) do before[a.path] = true end
    local function finish(status, parts)
        res.status = status
        local done, missing = {}, {}
        if status ~= "failed" then done, missing = M.register(root) end
        res.registered, res.missing = #done, missing
        if #missing > 0 then parts[#parts + 1] = "見つからなかったスクリプト：\n" .. table.concat(missing, "\n") end
        if status ~= "failed" and M.is_install(root) then
            M.set_root(root)
            res.old = M.old_registrations(root)
            if status == "installed" then
                res.presets_from = M.import_presets(root, res.old)
                if res.presets_from then parts[#parts + 1] = "前の EFXUI で保存したプリセットを引き継ぎました：\n" .. res.presets_from end
            end
            if #res.old > 0 then
                parts[#parts + 1] = "■ 前の版のマクロ\n別の場所のマクロが、まだ REAPER に登録されています（ショートカットは前のマクロを指したままです）：\n" ..
                    table.concat(res.old, "\n") .. "\n「前の版の登録を外す」を押すと登録だけ外します（ファイルは消しません）。" ..
                    "そのあと、新しいアクションにショートカットを割り当ててください。"
            end
            res.base_note = M.base_note(root)
            if res.base_note then parts[#parts + 1] = "■ AI の下書き\n" .. res.base_note end
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
    -- 2. 取ってきて、中身を確かめる（ここまでは何も書き換えない）
    local tmp = M.tmp_dir()
    M.rmdir(tmp)
    r.RecursiveCreateDirectory(tmp, 0)
    local src
    local ok
    ok, err = M.curl(M.zip_url(), tmp .. "\\pkg.zip", nil, root)
    if ok then src, err = M.extract(tmp .. "\\pkg.zip", tmp .. "\\x") end
    local new_list, copy, remove, data
    if src then
        local manifest = M.read_file(src .. "MANIFEST.txt")
        if not manifest then err = "ダウンロードした中に MANIFEST.txt がありません" else new_list, err = M.parse_manifest(manifest) end
    end
    if new_list then
        -- 今入っている版の一覧（無い・読めないときは、消すものは無しにする）
        local old_list, old_set = M.parse_manifest(M.read_file(root .. "MANIFEST.txt") or "")
        if not old_list then old_set = nil end
        copy, remove = M.plan(old_set, new_list, function(path) return r.file_exists(root .. win(path)) end)
        data, err = M.load_release(src, copy)
    end
    if not data then
        M.rmdir(tmp)
        res.error = err
        return finish("failed", {"更新できませんでした（何も書き換えていません。今の版のまま使えます）。\n\n" .. tostring(err)})
    end
    -- 3. 入れ替える。版の番号と一覧は最後に書く（途中で止まっても、もう一度押せばやり直せる）
    local failed = M.apply(root, copy, data, remove)
    if #failed == 0 then
        local a = M.write_file(root .. "MANIFEST.txt", M.read_file(src .. "MANIFEST.txt"))
        local b = M.write_file(root .. "VERSION.txt", M.read_file(src .. "VERSION.txt") or (latest .. "\n"))
        if not (a and b) then failed[#failed + 1] = "VERSION.txt / MANIFEST.txt" end
    end
    res.to = trim(M.read_file(src .. "VERSION.txt")) or latest
    M.rmdir(tmp)
    res.copied, res.removed = #copy, #remove
    if #failed > 0 then
        res.error = "書けなかった：" .. table.concat(failed, ", ")
        return finish("failed", {"書けないファイルがありました。開いているツール（ミキサー・EFX・ドラム）と AI の下書きを止めて、もう一度押してください。\n\n" ..
            table.concat(failed, "\n")})
    end
    local parts = {}
    local head = res.from and string.format("更新しました：%s → %s", res.from, res.to) or ("入れました（版 " .. res.to .. "）")
    parts[#parts + 1] = head .. string.format("\n場所：%s\n（%d ファイルを入れ替え、%d ファイルを消しました）", root, #copy, #remove)
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
    r.EnumerateFiles(new_root, -1)
    r.EnumerateSubdirectories(new_root, -1)
    if r.EnumerateFiles(new_root, 0) or r.EnumerateSubdirectories(new_root, 0) then
        return false, "移す先のフォルダが空ではありません：\n" .. new_root
    end
    local actions = M.actions(old_root)
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
    local old_paths = {}
    for _, a in ipairs(actions) do old_paths[#old_paths + 1] = old_root .. win(a.path) end
    M.unregister(old_paths)
    M.register(new_root)
    M.set_root(new_root)
    log(new_root, "moved %s -> %s", old_root, new_root)
    return true, "移しました：\n" .. new_root .. "\n\nアクションは新しい場所で登録し直しました。\n" ..
        "■ やること\n・ショートカットを割り当て直してください（REAPER は場所ごとに別のアクションとして覚えるため）。\n" ..
        "・REAPER を一度閉じて開き直してください。"
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
    ui.old = M.is_install(ui.root) and M.old_registrations(ui.root) or {}
end

local function do_pending()
    local what = ui.pending
    ui.pending = nil
    if what == "check" then
        refresh()
        ui.latest, ui.latest_err = M.latest_version(ui.root)
    elseif what == "run" then
        local res = M.run(ui.root)
        ui.message, ui.scroll = res.text, 0
        if res.to then ui.latest, ui.latest_err = res.to, nil end
        refresh()
    elseif what == "move" then
        local picked = M.pick_folder(ui.root)
        if picked then
            local new_root = M.root_in(picked)
            local installed = M.is_install(ui.root)
            local ask = installed and ("フォルダごと、次の場所へ移します。\n\n" .. new_root ..
                "\n\nアクションは新しい場所で登録し直します。ショートカットは割り当て直しになります。\n" ..
                "（AI の下書きの Python とモデルも一緒に移します。別のドライブへは数分かかります）") or ("次の場所に入れます。\n\n" .. new_root)
            if r.ShowMessageBox(ask, M.TITLE, 1) == 1 then
                local ok, msg = M.move(ui.root, new_root)
                ui.message, ui.scroll = msg, 0
                if ok then ui.root = new_root end
                refresh()
            end
        end
    elseif what == "old" then
        local ask = "次のマクロの登録を外します（ファイルは消しません。割り当てていたショートカットも外れます）。\n\n" .. table.concat(ui.old, "\n")
        if r.ShowMessageBox(ask, M.TITLE, 1) == 1 then
            M.unregister(ui.old)
            ui.message, ui.scroll = "前の版の登録を外しました。アクションリストで「XLaben」を探して、新しいアクションにショートカットを割り当ててください。", 0
            refresh()
        end
    elseif what == "open" then
        r.RecursiveCreateDirectory(ui.root, 0)
        r.ExecProcess(string.format('"%s" "%s"', (os.getenv("SystemRoot") or "C:\\Windows") .. "\\explorer.exe", (ui.root:gsub("\\$", ""))), -1)
    end
end

local BUSY = {check = "新しい版があるかを確かめています…", run = "ダウンロードして入れ替えています…（少し待つ）",
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
    if not installed then label = "インストールする"
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
    local base = {ok = {"使えます（Python とモデルあり）", C.ok}, none = {"Python とモデルがまだありません（全部入りの ZIP が要る）", C.dim},
                  old = {"Python とモデルが古い（新しい全部入りの ZIP が要る）", C.warn}, unused = {"—", C.dim}}
    local b = base[installed and ui.base or "unused"] or base.unused
    text_at(b[1], x + 110, y, b[2])
    y = y + ROW + 8
    -- ボタン
    local busy = ui.pending ~= nil
    if button(label, x, y, 250, 36, not busy, true) then ui.pending = "run" end
    if button("場所を変える", x + 262, y, 130, 36, not busy) then ui.pending = "move" end
    if button("フォルダを開く", x + 404, y, 130, 36, not busy) then ui.pending = "open" end
    y = y + 46
    if #(ui.old or {}) > 0 then
        text_at(string.format("前の版のマクロが %d 個、別の場所で登録されたままです", #ui.old), x, y + 6, C.warn)
        if button("前の版の登録を外す", x + w - 190, y, 190, 28, not busy) then ui.pending = "old" end
        y = y + 38
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
