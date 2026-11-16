-- @description X-Laben ツール：インストール・更新
-- @version 1.0
--
-- これ 1 つで「入れる」と「更新する」の両方をする。
--   ・初めて：空のフォルダ（英数字だけのパス。例 D:\XLabenTools）にこのファイルだけを置いて、REAPER で実行する
--     （アクション → アクションリストを表示 → 新規アクション → ReaScript を読み込む）。
--     全部入りの ZIP を展開した人は、その中のこのファイルを実行する。
--   ・更新：アクション「XLaben_Update」を実行する。新しい版があれば入れ替える。無ければ何もしない。
-- 入れ替えるのは、配っているファイルだけ（MANIFEST.txt に書いてあるもの）。次のものには触らない：
--   user フォルダ、ログ、自分で保存した EFX のプリセット、A2M の python・models（全部入りの ZIP にだけ入っている）。
-- 使うもの：Windows に最初から入っている curl.exe と tar.exe（Windows 10 の 1803 以降）。git は要らない。
-- 配る元が非公開のときは、user\github_token.txt に読み取り専用のトークンを1行で置く（このスクリプトは表示もログにも書かない）。

local r = reaper
local M = {}

M.SOURCE = {repo = "nemico/xl-tools", branch = "main"}  -- 配る元（GitHub の owner/name）。scripts/make_dist.py が配布のときに書き込む
M.TITLE = "X-Laben ツール"
M.ROOT = ((debug.getinfo(1, "S").source:match("^@?(.*[\\/])")) or ""):gsub("/", "\\")
M.TIMEOUT_MS = 320000       -- ダウンロードを待つ上限
M.CHANGES_LINES = 14        -- 終わったときに見せる「変更点」の行数

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

-- 非公開の元から取るときのトークン（user\github_token.txt の1行目）。無ければ nil
function M.token()
    local line = (M.read_file(M.ROOT .. "user\\github_token.txt") or ""):match("^[^\r\n]*")
    return trim(line)
end

function M.version_url()
    return "https://api.github.com/repos/" .. M.SOURCE.repo .. "/contents/VERSION.txt?ref=" .. M.SOURCE.branch
end

function M.zip_url()
    return "https://api.github.com/repos/" .. M.SOURCE.repo .. "/zipball/" .. M.SOURCE.branch
end

-- url を out_path に保存する。うまくいけば true、だめなら false と理由
function M.curl(url, out_path, accept)
    local cmd = string.format('"%s" -L -sS --fail --connect-timeout 15 --max-time 300 -o "%s"', sysexe("curl.exe"), out_path)
    if accept then cmd = cmd .. ' -H "Accept: ' .. accept .. '"' end
    local token = M.token()
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
    M.exec(string.format('"%s" /C rmdir /S /Q "%s"', sysexe("cmd.exe"), dir), 60000)
end

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

-- actions.txt（1行に「パス<TAB>説明」）のスクリプトをアクションに登録する。もう登録してあれば何も変わらない
function M.register(root)
    local done, missing = {}, {}
    for line in (M.read_file(root .. "actions.txt") or ""):gmatch("[^\r\n]+") do
        local path, label = line:match("^([^\t]+)\t(.*)$")
        if path then
            local full = root .. win(path)
            if r.file_exists(full) then
                r.AddRemoveReaScript(true, 0, full, true)
                done[#done + 1] = string.format("・%s（%s）", label, path:match("[^/]+$"))
            else
                missing[#missing + 1] = path
            end
        end
    end
    return done, missing
end

-- AI の下書きの土台（Python・モデル。全部入りの ZIP にだけ入っている）が合っているか。問題なければ nil
function M.base_note(root)
    local need = trim(M.read_file(root .. "A2M\\BASE_REQUIRED.txt"))
    if not need then return nil end
    local have = trim(M.read_file(root .. "A2M\\BASE.txt"))
    if not have then
        return "AI の下書きを使うには、全部入りの ZIP（Python とモデル入り）をこのフォルダに展開してください。\n" ..
            "ミキサー・EFX・ドラム・再生は、このままで使えます。"
    end
    if have ~= need then
        return "AI の下書きの土台（Python・モデル）が古いです。新しい全部入りの ZIP をもらって、このフォルダに上書きで展開し、\n" ..
            "もう一度この更新を実行してください。ミキサー・EFX・ドラム・再生は、このままで使えます。"
    end
    return nil
end

-- CHANGES.txt の頭の何行か
function M.changes(root)
    local out = {}
    for line in (M.read_file(root .. "CHANGES.txt") or ""):gsub("^\239\187\191", ""):gmatch("[^\r\n]+") do
        out[#out + 1] = line
        if #out >= M.CHANGES_LINES then break end
    end
    return table.concat(out, "\n")
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

local FIRST_TIME = "\n\n■ 初めての人\n" ..
    "・アクションリストで「XLaben」を探して、ショートカットを割り当てる（再生・停止の XLaben_Play.lua は Space に）。\n" ..
    "・REAPER の設定：オプション → 設定 → オーディオ →「停止中も FX を動かす（Run FX when stopped）」をオン。\n" ..
    "・使い方は、このフォルダの README.txt。"

-- 入れる・更新する。結果の表を返す：status = "installed" / "updated" / "latest" / "offline" / "failed"
function M.run()
    local root = M.ROOT
    local res = {from = trim(M.read_file(root .. "VERSION.txt"))}
    local function finish(status, text)
        res.status = status
        local done, missing = {}, {}
        if status ~= "failed" then done, missing = M.register(root) end
        res.registered, res.missing = #done, missing
        if #missing > 0 then text = text .. "\n\n見つからなかったスクリプト：\n" .. table.concat(missing, "\n") end
        local note = status ~= "failed" and M.base_note(root)
        if note then text = text .. "\n\n■ AI の下書き\n" .. note end
        res.base_note = note
        if status == "installed" then text = text .. FIRST_TIME end
        res.text = text
        log(root, "%s %s -> %s %s", status, res.from or "-", res.to or "-", res.error or "")
        return res
    end
    if root == "" or M.SOURCE.repo == "" then
        res.error = "配る元が書かれていません"
        return finish("failed", "このスクリプトには配る元（GitHub）が書かれていません。配られた XLaben_Update.lua を使ってください。")
    end
    local tmp = root .. "_update"
    M.rmdir(tmp)
    r.RecursiveCreateDirectory(tmp, 0)
    -- 1. 新しい版があるか
    local ok, err = M.curl(M.version_url(), tmp .. "\\VERSION.txt", "application/vnd.github.raw+json")
    local latest = ok and trim(M.read_file(tmp .. "\\VERSION.txt"))
    if latest and not latest:match("^[%w%.%-_]+$") then latest, err = nil, "版の番号が読めません" end
    if not latest then
        M.rmdir(tmp)
        res.error = err or "版の番号が空です"
        if res.from then
            return finish("offline", string.format("更新を確かめられませんでした（ネットにつながっていない、または配る元が見えません）。\n今の版（%s）のまま使えます。\n\n%s",
                res.from, res.error))
        end
        return finish("failed", "ダウンロードできませんでした。ネットにつながっているかを確かめて、もう一度実行してください。\n" ..
            "（配る元が非公開のときは、user\\github_token.txt が要ります）\n\n" .. res.error)
    end
    res.to = latest
    if latest == res.from then
        M.rmdir(tmp)
        return finish("latest", "最新です（版 " .. latest .. "）。")
    end
    -- 2. 取ってきて、中身を確かめる（ここまでは何も書き換えない）
    local src
    ok, err = M.curl(M.zip_url(), tmp .. "\\pkg.zip")
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
        return finish("failed", "更新できませんでした（何も書き換えていません。今の版のまま使えます）。\n\n" .. tostring(err))
    end
    -- 3. 入れ替える。版の番号と一覧は最後に書く（途中で止まっても、もう一度実行すればやり直せる）
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
        return finish("failed", "書けないファイルがありました。開いているツール（ミキサー・EFX・ドラム）と AI の下書きを止めて、もう一度実行してください。\n\n" ..
            table.concat(failed, "\n"))
    end
    local head = res.from and string.format("更新しました：%s → %s", res.from, res.to) or ("入れました（版 " .. res.to .. "）")
    local text = head .. string.format("\n（%d ファイルを入れ替え、%d ファイルを消しました）", #copy, #remove) ..
        "\n\nREAPER を一度閉じて開き直してください（開いていたツールは前の版のまま動いているため）。" ..
        "\n\n■ 変更点\n" .. M.changes(root)
    return finish(res.from and "updated" or "installed", text)
end

if XL_UPDATE_NO_RUN then return M end
local res = M.run()
r.ShowMessageBox(res.text, M.TITLE, 0)
return M
