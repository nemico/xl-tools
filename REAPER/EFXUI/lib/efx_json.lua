-- X-Laben EFX Controller：JSON を読む・書く（xlaben_efx_controller.lua から分けた）

-- ============================================================================
-- JSON Parser
-- ============================================================================
local json = {}

function json.decode(str)
    str = string.gsub(str, "^%s+", "")
    str = string.gsub(str, "%s+$", "")
    
    local first_char = string.sub(str, 1, 1)
    
    if first_char == "{" then
        return json.parse_object(str)
    elseif first_char == "[" then
        return json.parse_array(str)
    elseif first_char == '"' then
        return json.parse_string(str)
    elseif first_char == "t" then
        return true
    elseif first_char == "f" then
        return false
    elseif first_char == "n" then
        return nil
    else
        local num = tonumber(str)
        return num
    end
end

function json.encode(val)
    local t = type(val)
    if t == "number" then
        return tostring(val)
    elseif t == "boolean" then
        return val and "true" or "false"
    elseif t == "string" then
        return string.format("%q", val):gsub("\n", "\\n")
    elseif t == "table" then
        local count = 0
        local max_idx = 0
        for k, _ in pairs(val) do
            count = count + 1
            if type(k) == "number" and k > max_idx then
                max_idx = k
            end
        end
        local is_arr = (count > 0 and count == max_idx)
        local parts = {}
        if is_arr then
            for i = 1, max_idx do
                table.insert(parts, json.encode(val[i]))
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            for k, v in pairs(val) do
                table.insert(parts, string.format("%q:%s", tostring(k), json.encode(v)))
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    else
        return "null"
    end
end

function json.parse_object(str)
    local obj = {}
    local pos = 2
    
    while pos <= #str do
        while pos <= #str and string.match(string.sub(str, pos, pos), "%s") do
            pos = pos + 1
        end
        
        if string.sub(str, pos, pos) == "}" then
            break
        end
        
        local key, new_pos = json.parse_string_value(str, pos)
        if not key then break end
        pos = new_pos
        
        while pos <= #str and string.match(string.sub(str, pos, pos), "[%s:]") do
            pos = pos + 1
        end
        
        local value, new_pos = json.parse_value(str, pos)
        pos = new_pos
        
        obj[key] = value
        
        while pos <= #str and string.match(string.sub(str, pos, pos), "%s") do
            pos = pos + 1
        end
        
        if string.sub(str, pos, pos) == "," then
            pos = pos + 1
        end
    end
    
    return obj
end

function json.parse_array(str)
    local arr = {}
    local pos = 2
    
    while pos <= #str do
        while pos <= #str and string.match(string.sub(str, pos, pos), "%s") do
            pos = pos + 1
        end
        
        if string.sub(str, pos, pos) == "]" then
            break
        end
        
        local value, new_pos = json.parse_value(str, pos)
        pos = new_pos
        
        table.insert(arr, value)
        
        while pos <= #str and string.match(string.sub(str, pos, pos), "%s") do
            pos = pos + 1
        end
        
        if string.sub(str, pos, pos) == "," then
            pos = pos + 1
        end
    end
    
    return arr
end

function json.parse_string_value(str, pos)
    if string.sub(str, pos, pos) ~= '"' then
        return nil, pos
    end
    pos = pos + 1
    
    local result = ""
    
    while pos <= #str do
        local char = string.sub(str, pos, pos)
        
        if char == '"' then
            return result, pos + 1
        elseif char == "\\" then
            pos = pos + 1
            local next_char = string.sub(str, pos, pos)
            if next_char == "n" then
                result = result .. "\n"
            elseif next_char == "t" then
                result = result .. "\t"
            elseif next_char == "r" then
                result = result .. "\r"
            else
                result = result .. next_char
            end
        else
            result = result .. char
        end
        
        pos = pos + 1
    end
    
    return result, pos
end

function json.parse_value(str, pos)
    while pos <= #str and string.match(string.sub(str, pos, pos), "%s") do
        pos = pos + 1
    end
    
    local char = string.sub(str, pos, pos)
    
    if char == "{" then
        local obj_end = json.find_matching_bracket(str, pos, "{", "}")
        local obj_str = string.sub(str, pos, obj_end)
        return json.parse_object(obj_str), obj_end + 1
    elseif char == "[" then
        local arr_end = json.find_matching_bracket(str, pos, "[", "]")
        local arr_str = string.sub(str, pos, arr_end)
        return json.parse_array(arr_str), arr_end + 1
    elseif char == '"' then
        return json.parse_string_value(str, pos)
    elseif char == "t" then
        return true, pos + 4
    elseif char == "f" then
        return false, pos + 5
    elseif char == "n" then
        return nil, pos + 4
    else
        local num_end = pos
        while num_end <= #str and string.match(string.sub(str, num_end, num_end), "[%d%.%+%-eE]") do
            num_end = num_end + 1
        end
        local num_str = string.sub(str, pos, num_end - 1)
        return tonumber(num_str), num_end
    end
end

function json.find_matching_bracket(str, pos, open_char, close_char)
    local depth = 0
    local in_string = false
    
    while pos <= #str do
        local char = string.sub(str, pos, pos)
        
        if char == '"' and string.sub(str, pos - 1, pos - 1) ~= "\\" then
            in_string = not in_string
        elseif not in_string then
            if char == open_char then
                depth = depth + 1
            elseif char == close_char then
                depth = depth - 1
                if depth == 0 then
                    return pos
                end
            end
        end
        
        pos = pos + 1
    end
    
    return pos
end

function json.load_file(filename)
    local file = io.open(filename, "r")
    if not file then
        return nil, "Cannot open file: " .. filename
    end

    local content = file:read("*all")
    file:close()

    return json.decode(content)
end

-- ---------------------------------------------------------------- 速く読む（読んだ結果を Lua のファイルにしておく）
-- 大きな JSON（xlaben_efx_data.json、約 1MB）を、この部品で読むと 0.4 秒ほどかかる（開くたび・切り替えるたび）。
-- 一度読んだ結果を Lua の表のファイル（cache_path）に書いておき、次からはそれを dofile で読む（REAPER の Lua が直接読むので速い）。
-- JSON の大きさが変わったら（直したら）読み直して書き直す。中身は、この部品で読んだものとまったく同じ（null は入らない、など）

local function lua_literal(v, out)
    local t = type(v)
    if t == "string" then
        out[#out + 1] = string.format("%q", v)
    elseif t == "number" then
        if math.type(v) == "integer" then
            out[#out + 1] = string.format("%d", v)
        else
            local s = string.format("%.17g", v)
            if not s:find("[%.eEn]") then s = s .. ".0" end  -- 5.0 は小数のまま（表示が変わらないように）
            out[#out + 1] = s
        end
    elseif t == "boolean" then
        out[#out + 1] = v and "true" or "false"
    elseif t == "table" then
        out[#out + 1] = "{"
        local n = #v
        for i = 1, n do
            lua_literal(v[i], out)
            out[#out + 1] = ","
        end
        for k, x in pairs(v) do
            if not (math.type(k) == "integer" and k >= 1 and k <= n) then
                out[#out + 1] = "["
                lua_literal(k, out)
                out[#out + 1] = "]="
                lua_literal(x, out)
                out[#out + 1] = ","
            end
        end
        out[#out + 1] = "}"
    else
        out[#out + 1] = "nil"
    end
end

-- data を、読むと同じ表になる Lua のソースにする
function json.to_lua(data)
    local out = {}
    lua_literal(data, out)
    return "return " .. table.concat(out)
end

local function file_size(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local size = f:seek("end")
    f:close()
    return size
end

-- json_path を読む。cache_path に同じ大きさの JSON から作ったものがあれば、そちらを読む（速い）
function json.load_cached(json_path, cache_path)
    local size = file_size(json_path)
    if not size then return nil, "Cannot open file: " .. json_path end
    local f = io.open(cache_path, "rb")
    if f then
        local head = f:read("l") or ""
        f:close()
        if head == "-- json_size=" .. size then
            local ok, data = pcall(dofile, cache_path)
            if ok and type(data) == "table" then return data, nil, "cache" end
        end
    end
    local data, err = json.load_file(json_path)
    if not data then return nil, err end
    local w = io.open(cache_path, "wb")
    if w then
        w:write("-- json_size=" .. size .. "\n-- " .. json_path:match("[^/\\]+$") .. " を読んだ結果（efx_json.load_cached が作る。手で直さない）\n")
        w:write(json.to_lua(data))
        w:close()
    end
    return data, nil, "json"
end

function json.save_file(filename, data)
    local file = io.open(filename, "w")
    if not file then
        return false, "Cannot open file for writing: " .. filename
    end
    local str = json.encode(data)
    file:write(str)
    file:close()
    return true
end



return json
