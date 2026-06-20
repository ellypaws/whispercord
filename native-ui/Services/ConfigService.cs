using System.Text.Json;
using System.Text.Json.Nodes;

namespace DiscordTranscriber.Native.Services;

public sealed class ConfigService
{
    private readonly RepositoryPaths _paths;
    private JsonObject _root = new();

    public ConfigService(RepositoryPaths paths)
    {
        _paths = paths;
        Load();
    }

    public JsonObject Root => _root;

    public void Load()
    {
        if (!File.Exists(_paths.ConfigPath))
        {
            _root = new();
            return;
        }

        try
        {
            var node = JsonNode.Parse(File.ReadAllText(_paths.ConfigPath));
            _root = node as JsonObject ?? new JsonObject();
        }
        catch
        {
            _root = new();
        }
    }

    public async Task SaveAsync()
    {
        var options = new JsonSerializerOptions { WriteIndented = true };
        await File.WriteAllTextAsync(_paths.ConfigPath, _root.ToJsonString(options)).ConfigureAwait(false);
    }

    public string GetString(string key, string fallback = "") => TryValue<string>(_root[key], out var value) ? value ?? fallback : fallback;
    public int GetInt(string key, int fallback) => TryValue<int>(_root[key], out var value) ? value : fallback;
    public double GetDouble(string key, double fallback) => TryValue<double>(_root[key], out var value) ? value : fallback;
    public bool GetBool(string key, bool fallback) => TryValue<bool>(_root[key], out var value) ? value : fallback;

    public string GetNestedString(string section, string key, string fallback = "")
    {
        return GetObject(section).TryGetPropertyValue(key, out var node) && TryValue<string>(node, out var value)
            ? value ?? fallback
            : fallback;
    }

    public int GetNestedInt(string section, string key, int fallback)
    {
        return GetObject(section).TryGetPropertyValue(key, out var node) && TryValue<int>(node, out var value)
            ? value
            : fallback;
    }

    public double GetNestedDouble(string section, string key, double fallback)
    {
        return GetObject(section).TryGetPropertyValue(key, out var node) && TryValue<double>(node, out var value)
            ? value
            : fallback;
    }

    public bool GetNestedBool(string section, string key, bool fallback)
    {
        return GetObject(section).TryGetPropertyValue(key, out var node) && TryValue<bool>(node, out var value)
            ? value
            : fallback;
    }

    public IReadOnlyList<string> GetNestedStringArray(string section, string key, IReadOnlyList<string> fallback)
    {
        if (!GetObject(section).TryGetPropertyValue(key, out var node) || node is not JsonArray array)
        {
            return fallback;
        }

        return array.Select(x => TryValue<string>(x, out var value) ? value : null)
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .Cast<string>()
            .ToList();
    }

    public void Set(string key, object? value)
    {
        _root[key] = ToNode(value);
    }

    public void SetNested(string section, string key, object? value)
    {
        GetObject(section)[key] = ToNode(value);
    }

    public bool GetOverlayFor(string exe)
    {
        var node = _root["inject_overlay"];
        if (TryValue<bool>(node, out var global))
        {
            return global;
        }

        if (node is JsonObject obj &&
            obj.TryGetPropertyValue(exe, out var perClient) &&
            TryValue<bool>(perClient, out var value))
        {
            return value;
        }

        return true;
    }

    public void SetOverlayFor(string exe, bool enabled)
    {
        if (_root["inject_overlay"] is not JsonObject obj)
        {
            obj = new JsonObject();
            _root["inject_overlay"] = obj;
        }

        obj[exe] = JsonValue.Create(enabled);
    }

    public bool GetSelfFor(string exe)
    {
        var clients = GetObject("self_transcribe").TryGetPropertyValue("clients", out var node) && node is JsonObject obj
            ? obj
            : new JsonObject();

        return clients.TryGetPropertyValue(exe, out var perClient) && TryValue<bool>(perClient, out var value)
            ? value
            : true;
    }

    public void SetSelfFor(string exe, bool enabled)
    {
        var self = GetObject("self_transcribe");
        if (self["clients"] is not JsonObject clients)
        {
            clients = new JsonObject();
            self["clients"] = clients;
        }

        clients[exe] = JsonValue.Create(enabled);
    }

    private JsonObject GetObject(string key)
    {
        if (_root[key] is JsonObject obj)
        {
            return obj;
        }

        obj = new JsonObject();
        _root[key] = obj;
        return obj;
    }

    private static bool TryValue<T>(JsonNode? node, out T? value)
    {
        try
        {
            if (node is null)
            {
                value = default;
                return false;
            }

            value = node.GetValue<T>();
            return true;
        }
        catch
        {
            value = default;
            return false;
        }
    }

    private static JsonNode? ToNode(object? value)
    {
        return value switch
        {
            null => null,
            JsonNode node => node,
            string text => JsonValue.Create(text),
            int number => JsonValue.Create(number),
            double number => JsonValue.Create(number),
            bool flag => JsonValue.Create(flag),
            _ => JsonSerializer.SerializeToNode(value)
        };
    }
}
