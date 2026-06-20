using System.Collections.ObjectModel;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using DiscordTranscriber.Native.Models;
using DiscordTranscriber.Native.Services;
using Microsoft.UI.Dispatching;

namespace DiscordTranscriber.Native.ViewModels;

public sealed class MainViewModel : ObservableObject, IDisposable
{
    private readonly DispatcherQueue _dispatcher;
    private readonly RepositoryPaths _paths = new();
    private readonly ConfigService _config;
    private readonly BackendService _backend;
    private readonly RelayService _relay = new();
    private readonly DiscordClientService _clients = new();
    private readonly CudaService _cuda;
    private readonly InputDeviceService _inputDevices;
    private readonly UpdateService _update = new();
    private bool _loadingConfig;

    private string _backendState = "stopped";
    private string _relayState = "relay off";
    private string _activeStreams = "0 streams";
    private string _gpuStatus = "";
    private string _progressText = "";
    private bool _backendRunning;
    private bool _restartNeeded;

    private string _whisperModel = "small";
    private string _language = "";
    private int _beamSize = 1;
    private string _device = "cuda";
    private string _computeType = "float16";
    private int _relayPort = 8765;
    private double _minRmsDbfs = -50;
    private bool _vad = true;
    private string _dropPhrases = "";
    private bool _alertSound = true;
    private string _highlight = "#f04747";
    private bool _voiceEvents = true;
    private bool _newestAtTop;
    private string _updateStatus = "Checking for updates…";
    private bool _updateAvailable;
    private bool _showTimestamps;
    private string _timestampFormat = "clock";
    private int _overlayLogHeight = 300;
    private int _subtitleTimeout = 8000;
    private int _maxBlocks = 6;
    private int _fadeStart = 5;
    private double _minFadeOpacity = 0.25;
    private bool _selfEnabled;
    private bool _selfOnlyUnmuted = true;
    private bool _selfRequireSpeaking = true;
    private string _selfDevice = "";

    public MainViewModel(DispatcherQueue dispatcher)
    {
        _dispatcher = dispatcher;
        _config = new ConfigService(_paths);
        _backend = new BackendService(_paths);
        _cuda = new CudaService(_paths);
        _inputDevices = new InputDeviceService(_paths);

        _backend.LogLine += line => Enqueue(() => AddLog(line));
        _backend.StateChanged += state => Enqueue(() =>
        {
            BackendState = state;
            BackendRunning = state == "running";
        });
        _backend.ProgressChanged += text => Enqueue(() => ProgressText = text);
        _relay.StateChanged += state => Enqueue(() => RelayState = state);
        _relay.MessageReceived += msg => Enqueue(() => HandleRelayMessage(msg));
        Panels.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(IsSingleClient));
            OnPropertyChanged(nameof(HasMultipleClients));
            OnPropertyChanged(nameof(PrimaryPanel));
        };
        KeywordTokens.CollectionChanged += (_, _) =>
        {
            if (!_loadingConfig)
            {
                RestartNeeded = true;
            }
        };
        LoadConfigToProperties();
    }

    public ObservableCollection<DiscordClientInfo> Clients { get; } = new();
    public ObservableCollection<TranscriptPanel> Panels { get; } = new();

    // The transcript collapses to a single full-width card unless more than one Discord
    // client is actually producing transcripts.
    public bool IsSingleClient => Panels.Count <= 1;
    public bool HasMultipleClients => Panels.Count > 1;
    public TranscriptPanel? PrimaryPanel => Panels.Count > 0 ? Panels[0] : null;
    public ObservableCollection<string> Logs { get; } = new();
    public ObservableCollection<InputDeviceInfo> InputDevices { get; } = new();
    public ObservableCollection<string> KeywordTokens { get; } = new();

    public IReadOnlyList<string> WhisperModels { get; } = new[] { "tiny", "base", "small", "medium", "large-v3" };
    public IReadOnlyList<string> Devices { get; } = new[] { "cuda", "cpu" };
    public IReadOnlyList<string> ComputeTypes { get; } = new[] { "float16", "int8_float16", "int8", "float32" };
    public IReadOnlyList<string> TimestampFormats { get; } = new[] { "clock", "relative" };

    public string BackendState { get => _backendState; set => SetProperty(ref _backendState, value); }
    public string RelayState { get => _relayState; set => SetProperty(ref _relayState, value); }
    public string ActiveStreams { get => _activeStreams; set => SetProperty(ref _activeStreams, value); }
    public string GpuStatus { get => _gpuStatus; set => SetProperty(ref _gpuStatus, value); }
    public string ProgressText { get => _progressText; set => SetProperty(ref _progressText, value); }
    public bool BackendRunning { get => _backendRunning; set => SetProperty(ref _backendRunning, value); }
    public bool RestartNeeded { get => _restartNeeded; set => SetProperty(ref _restartNeeded, value); }

    public string WhisperModel { get => _whisperModel; set => SetConfigProperty(ref _whisperModel, value); }
    public string Language { get => _language; set => SetConfigProperty(ref _language, value); }
    public int BeamSize { get => _beamSize; set => SetConfigProperty(ref _beamSize, value); }
    public string Device { get => _device; set => SetConfigProperty(ref _device, value); }
    public string ComputeType { get => _computeType; set => SetConfigProperty(ref _computeType, value); }
    public int RelayPort { get => _relayPort; set => SetConfigProperty(ref _relayPort, value); }
    public double MinRmsDbfs { get => _minRmsDbfs; set => SetConfigProperty(ref _minRmsDbfs, value); }
    public bool Vad { get => _vad; set => SetConfigProperty(ref _vad, value); }
    public string DropPhrases { get => _dropPhrases; set => SetConfigProperty(ref _dropPhrases, value); }
    public string AppVersion => _update.CurrentVersion;
    public string UpdateStatus { get => _updateStatus; set => SetProperty(ref _updateStatus, value); }
    public bool UpdateAvailable { get => _updateAvailable; set => SetProperty(ref _updateAvailable, value); }
    public bool AlertSound { get => _alertSound; set => SetConfigProperty(ref _alertSound, value); }
    public string Highlight { get => _highlight; set => SetConfigProperty(ref _highlight, value); }
    public bool VoiceEvents { get => _voiceEvents; set => SetConfigProperty(ref _voiceEvents, value); }
    public bool NewestAtTop { get => _newestAtTop; set => SetConfigProperty(ref _newestAtTop, value); }
    public bool ShowTimestamps { get => _showTimestamps; set => SetConfigProperty(ref _showTimestamps, value); }
    public string TimestampFormat { get => _timestampFormat; set => SetConfigProperty(ref _timestampFormat, value); }
    public int OverlayLogHeight { get => _overlayLogHeight; set => SetConfigProperty(ref _overlayLogHeight, value); }
    public int SubtitleTimeout { get => _subtitleTimeout; set => SetConfigProperty(ref _subtitleTimeout, value); }
    public int MaxBlocks { get => _maxBlocks; set => SetConfigProperty(ref _maxBlocks, value); }
    public int FadeStart { get => _fadeStart; set => SetConfigProperty(ref _fadeStart, value); }
    public double MinFadeOpacity { get => _minFadeOpacity; set => SetConfigProperty(ref _minFadeOpacity, value); }
    public bool SelfEnabled { get => _selfEnabled; set => SetConfigProperty(ref _selfEnabled, value); }
    public bool SelfOnlyUnmuted { get => _selfOnlyUnmuted; set => SetConfigProperty(ref _selfOnlyUnmuted, value); }
    public bool SelfRequireSpeaking { get => _selfRequireSpeaking; set => SetConfigProperty(ref _selfRequireSpeaking, value); }
    public string SelfDevice { get => _selfDevice; set => SetConfigProperty(ref _selfDevice, value); }

    public async Task InitializeAsync()
    {
        await RefreshInputDevicesAsync().ConfigureAwait(false);
        await RefreshClientsAsync().ConfigureAwait(false);
        Enqueue(() => GpuStatus = _cuda.RuntimePresent() ? "GPU runtime: ready" : "GPU runtime: will download on first Start");
        await _relay.StartAsync(RelayPort).ConfigureAwait(false);

        var info = await _update.CheckAsync().ConfigureAwait(false);
        Enqueue(() =>
        {
            UpdateStatus = info.Status;
            UpdateAvailable = info.UpdateAvailable;
        });
    }

    public async Task StartBackendAsync()
    {
        await _backend.StartAsync().ConfigureAwait(false);
        await _relay.StartAsync(RelayPort).ConfigureAwait(false);
    }

    public async Task StopBackendAsync()
    {
        await _backend.StopAsync().ConfigureAwait(false);
    }

    public async Task RestartBackendAsync()
    {
        await SaveSettingsAsync().ConfigureAwait(false);
        await _backend.StopAsync().ConfigureAwait(false);
        await _backend.StartAsync().ConfigureAwait(false);
        await _relay.StartAsync(RelayPort).ConfigureAwait(false);
        Enqueue(() => RestartNeeded = false);
    }

    public async Task RefreshClientsAsync()
    {
        var list = await _clients.ListClientsAsync(_config).ConfigureAwait(false);
        Enqueue(() =>
        {
            Clients.Clear();
            foreach (var client in list)
            {
                Clients.Add(client);
                _ = PanelFor(client.Exe);
            }
        });
    }

    public async Task EnsureClientAsync(DiscordClientInfo client)
    {
        var restart = client.IsRunning && !client.IsLive;
        var status = await _clients.EnsureClientAsync(client, restart).ConfigureAwait(false);
        Enqueue(() => AddLog($"[native] {client.DisplayName}: {status}"));
        await RefreshClientsAsync().ConfigureAwait(false);
    }

    public async Task SaveSettingsAsync()
    {
        WritePropertiesToConfig();
        await _config.SaveAsync().ConfigureAwait(false);
        Enqueue(() => AddLog("[native] settings saved"));
    }

    public void ClearConsole() => Logs.Clear();

    public void ClearPanel(TranscriptPanel panel)
    {
        panel.Items.Clear();
        panel.CurrentByUser.Clear();
        panel.FinalCount = 0;
    }

    private async Task RefreshInputDevicesAsync()
    {
        var devices = await _inputDevices.ListAsync().ConfigureAwait(false);
        Enqueue(() =>
        {
            InputDevices.Clear();
            foreach (var device in devices)
            {
                InputDevices.Add(device);
            }
        });
    }

    private void LoadConfigToProperties()
    {
        _loadingConfig = true;
        WhisperModel = _config.GetString("whisper_model", "small");
        Language = _config.GetString("language", "");
        BeamSize = _config.GetInt("beam_size", 1);
        Device = _config.GetString("device", "cuda");
        ComputeType = _config.GetString("compute_type", "float16");
        RelayPort = _config.GetInt("relay_port", 8765);
        MinRmsDbfs = _config.GetNestedDouble("gating", "min_rms_dbfs", -50);
        Vad = _config.GetNestedBool("gating", "vad", true);
        DropPhrases = string.Join(", ", _config.GetNestedStringArray("gating", "drop_phrases", Array.Empty<string>()));
        KeywordTokens.Clear();
        foreach (var keyword in _config.GetNestedStringArray("alerts", "keywords", Array.Empty<string>()))
        {
            KeywordTokens.Add(keyword);
        }
        AlertSound = _config.GetNestedBool("alerts", "sound", true);
        Highlight = _config.GetNestedString("alerts", "highlight", "#f04747");
        VoiceEvents = _config.GetBool("voice_events", true);
        NewestAtTop = _config.GetNestedBool("ui", "newest_at_top", false);
        ShowTimestamps = _config.GetNestedBool("ui", "show_timestamps", false);
        TimestampFormat = _config.GetNestedString("ui", "timestamp_format", "clock");
        OverlayLogHeight = _config.GetNestedInt("overlay", "log_height", 300);
        SubtitleTimeout = _config.GetNestedInt("overlay", "subtitle_timeout_ms", 8000);
        MaxBlocks = _config.GetNestedInt("overlay", "max_blocks", 6);
        FadeStart = _config.GetNestedInt("overlay", "fade_start_count", 5);
        MinFadeOpacity = _config.GetNestedDouble("overlay", "min_fade_opacity", 0.25);
        SelfEnabled = _config.GetNestedBool("self_transcribe", "enabled", false);
        SelfOnlyUnmuted = _config.GetNestedBool("self_transcribe", "only_when_unmuted", true);
        SelfRequireSpeaking = _config.GetNestedBool("self_transcribe", "require_discord_speaking", true);
        SelfDevice = ReadSelfDevice();
        _loadingConfig = false;
        RestartNeeded = false;
    }

    private void WritePropertiesToConfig()
    {
        _config.Set("whisper_model", WhisperModel);
        _config.Set("language", Language);
        _config.Set("beam_size", BeamSize);
        _config.Set("device", Device);
        _config.Set("compute_type", ComputeType);
        _config.Set("relay_port", RelayPort);
        _config.SetNested("gating", "min_rms_dbfs", MinRmsDbfs);
        _config.SetNested("gating", "vad", Vad);
        _config.SetNested("gating", "drop_phrases", ToJsonArray(DropPhrases));
        _config.SetNested("alerts", "keywords", ToJsonArray(KeywordTokens));
        _config.SetNested("alerts", "sound", AlertSound);
        _config.SetNested("alerts", "highlight", string.IsNullOrWhiteSpace(Highlight) ? "#f04747" : Highlight);
        _config.Set("voice_events", VoiceEvents);
        _config.SetNested("ui", "newest_at_top", NewestAtTop);
        _config.SetNested("ui", "show_timestamps", ShowTimestamps);
        _config.SetNested("ui", "timestamp_format", TimestampFormat);
        _config.SetNested("overlay", "log_height", OverlayLogHeight);
        _config.SetNested("overlay", "subtitle_timeout_ms", SubtitleTimeout);
        _config.SetNested("overlay", "max_blocks", MaxBlocks);
        _config.SetNested("overlay", "fade_start_count", FadeStart);
        _config.SetNested("overlay", "min_fade_opacity", MinFadeOpacity);
        _config.SetNested("self_transcribe", "enabled", SelfEnabled);
        _config.SetNested("self_transcribe", "only_when_unmuted", SelfOnlyUnmuted);
        _config.SetNested("self_transcribe", "require_discord_speaking", SelfRequireSpeaking);
        _config.SetNested("self_transcribe", "device", string.IsNullOrWhiteSpace(SelfDevice) ? null : ParseDevice(SelfDevice));

        foreach (var client in Clients)
        {
            _config.SetOverlayFor(client.Exe, client.OverlayEnabled);
            _config.SetSelfFor(client.Exe, client.SelfEnabled);
        }
    }

    private void HandleRelayMessage(JsonObject msg)
    {
        switch (GetString(msg, "type"))
        {
            case "status":
                HandleStatus(msg);
                break;
            case "transcript":
                HandleTranscript(msg);
                break;
            case "event":
                HandleEvent(msg);
                break;
            case "rename":
                HandleRename(msg);
                break;
        }
    }

    private void HandleStatus(JsonObject msg)
    {
        var active = GetInt(msg, "active");
        ActiveStreams = active == 1 ? "1 stream" : $"{active} streams";
        if (msg["clients"] is not JsonObject clients)
        {
            return;
        }

        foreach (var (key, node) in clients)
        {
            if (node is not JsonObject status)
            {
                continue;
            }

            var client = Clients.FirstOrDefault(c => c.Exe.Equals(key, StringComparison.OrdinalIgnoreCase));
            if (client is not null)
            {
                client.IsHooked = GetBool(status, "hooked");
                client.HasCdp = GetBool(status, "cdp");
                client.Streams = GetInt(status, "streams");
                client.Active = GetInt(status, "active");
                client.Mapped = GetInt(status, "mapped");
            }

            PanelFor(key).Active = GetInt(status, "active");
        }
    }

    private void HandleTranscript(JsonObject msg)
    {
        var client = GetString(msg, "client", "unknown").ToLowerInvariant();
        var userId = GetString(msg, "userId", Guid.NewGuid().ToString("N"));
        var text = GetString(msg, "text");
        var isFinal = GetBool(msg, "isFinal");
        var panel = PanelFor(client);

        if (!panel.CurrentByUser.TryGetValue(userId, out var row))
        {
            row = new TranscriptItem
            {
                Client = client,
                UserId = userId,
                Name = GetString(msg, "name", "user " + Tail(userId)),
                Avatar = GetString(msg, "avatar"),
                Timestamp = FromMilliseconds(GetLong(msg, "ts")),
                IsFinal = isFinal
            };
            InsertTranscript(panel, row);
            panel.CurrentByUser[userId] = row;
        }

        row.Name = GetString(msg, "name", row.Name);
        row.Avatar = GetString(msg, "avatar", row.Avatar ?? "");
        row.Text = string.IsNullOrEmpty(text) && !isFinal ? "..." : text;
        row.Timestamp = FromMilliseconds(GetLong(msg, "ts"));
        row.IsFinal = isFinal;

        if (isFinal)
        {
            panel.CurrentByUser.Remove(userId);
            if (string.IsNullOrWhiteSpace(text))
            {
                panel.Items.Remove(row);
            }
            else
            {
                panel.FinalCount++;
            }
        }

        TrimPanel(panel);
    }

    private void HandleEvent(JsonObject msg)
    {
        if (!VoiceEvents)
        {
            return;
        }

        var client = GetString(msg, "client", "unknown").ToLowerInvariant();
        var kind = GetString(msg, "event");
        var name = GetString(msg, "name", "someone");
        var row = new TranscriptItem
        {
            IsEvent = true,
            IsFinal = true,
            Client = client,
            UserId = GetString(msg, "userId"),
            Name = name,
            Avatar = GetString(msg, "avatar"),
            Text = EventText(kind, name),
            Timestamp = FromMilliseconds(GetLong(msg, "ts"))
        };

        var panel = PanelFor(client);
        InsertTranscript(panel, row);
        TrimPanel(panel);
    }

    private void HandleRename(JsonObject msg)
    {
        var client = GetString(msg, "client", "unknown").ToLowerInvariant();
        var userId = GetString(msg, "userId");
        var name = GetString(msg, "name");
        var avatar = GetString(msg, "avatar");
        var panel = PanelFor(client);

        foreach (var item in panel.Items.Where(i => i.UserId == userId))
        {
            if (!string.IsNullOrWhiteSpace(name))
            {
                item.Name = name;
            }

            if (!string.IsNullOrWhiteSpace(avatar))
            {
                item.Avatar = avatar;
            }
        }
    }

    private TranscriptPanel PanelFor(string? client)
    {
        var key = string.IsNullOrWhiteSpace(client) ? "unknown" : client.ToLowerInvariant();
        var panel = Panels.FirstOrDefault(p => p.ClientKey == key);
        if (panel is not null)
        {
            return panel;
        }

        panel = new TranscriptPanel { ClientKey = key, Title = ClientLabel(key), NewestAtTop = NewestAtTop };
        Panels.Add(panel);
        return panel;
    }

    public void FlipPanelDirection(TranscriptPanel panel)
    {
        panel.NewestAtTop = !panel.NewestAtTop;
        var reversed = panel.Items.Reverse().ToList();
        panel.Items.Clear();
        foreach (var item in reversed)
        {
            panel.Items.Add(item);
        }
    }

    private void InsertTranscript(TranscriptPanel panel, TranscriptItem row)
    {
        if (panel.NewestAtTop)
        {
            panel.Items.Insert(0, row);
        }
        else
        {
            panel.Items.Add(row);
        }
    }

    private void TrimPanel(TranscriptPanel panel)
    {
        while (panel.Items.Count > 200)
        {
            panel.Items.RemoveAt(panel.NewestAtTop ? panel.Items.Count - 1 : 0);
        }
    }

    private void AddLog(string line)
    {
        Logs.Add(line);
        while (Logs.Count > 600)
        {
            Logs.RemoveAt(0);
        }
    }

    private void Enqueue(Action action)
    {
        if (!_dispatcher.TryEnqueue(() => action()))
        {
            action();
        }
    }

    private bool SetConfigProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        var changed = SetProperty(ref field, value, propertyName);
        if (changed && !_loadingConfig)
        {
            RestartNeeded = true;
        }

        return changed;
    }

    private string ReadSelfDevice()
    {
        var node = _config.Root["self_transcribe"] is JsonObject self ? self["device"] : null;
        if (node is null)
        {
            return "";
        }

        try
        {
            return node.GetValue<int>().ToString();
        }
        catch
        {
            try
            {
                return node.GetValue<string>() ?? "";
            }
            catch
            {
                return "";
            }
        }
    }

    private static JsonArray ToJsonArray(string csv)
    {
        var arr = new JsonArray();
        foreach (var item in csv.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries))
        {
            arr.Add(item);
        }

        return arr;
    }

    private static JsonArray ToJsonArray(IEnumerable<string> items)
    {
        var arr = new JsonArray();
        foreach (var item in items)
        {
            var trimmed = item.Trim();
            if (trimmed.Length > 0)
            {
                arr.Add(trimmed);
            }
        }

        return arr;
    }

    private static JsonNode? ParseDevice(string value)
    {
        return int.TryParse(value, out var index) ? JsonValue.Create(index) : JsonValue.Create(value);
    }

    private static string ClientLabel(string key) => key switch
    {
        "discordptb.exe" => "Discord PTB",
        "discord.exe" => "Discord",
        "discordcanary.exe" => "Discord Canary",
        "discorddevelopment.exe" => "Discord Dev",
        _ => "Unknown"
    };

    private static string EventText(string kind, string name) => kind switch
    {
        "joined" => $"{name} joined the channel",
        "left" => $"{name} left the channel",
        "muted" => $"{name} muted",
        "unmuted" => $"{name} unmuted",
        "deafened" => $"{name} deafened",
        "undeafened" => $"{name} undeafened",
        "video_on" => $"{name} turned camera on",
        "video_off" => $"{name} turned camera off",
        "stream_on" => $"{name} started streaming",
        "stream_off" => $"{name} stopped streaming",
        _ => $"{name} {kind}"
    };

    private static string Tail(string value) => value.Length <= 5 ? value : value[^5..];
    private static DateTimeOffset FromMilliseconds(long value) => value <= 0 ? DateTimeOffset.Now : DateTimeOffset.FromUnixTimeMilliseconds(value);

    private static string GetString(JsonObject obj, string key, string fallback = "")
    {
        try { return obj[key]?.GetValue<string>() ?? fallback; }
        catch { return fallback; }
    }

    private static int GetInt(JsonObject obj, string key)
    {
        try { return obj[key]?.GetValue<int>() ?? 0; }
        catch { return 0; }
    }

    private static long GetLong(JsonObject obj, string key)
    {
        try { return obj[key]?.GetValue<long>() ?? 0; }
        catch { return 0; }
    }

    private static bool GetBool(JsonObject obj, string key)
    {
        try { return obj[key]?.GetValue<bool>() ?? false; }
        catch { return false; }
    }

    public void Dispose()
    {
        _relay.Dispose();
        _backend.Dispose();
    }
}
