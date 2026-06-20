namespace DiscordTranscriber.Native.Models;

public sealed class DiscordClientInfo : ObservableObject
{
    private bool _isLive;
    private bool _isRunning;
    private bool _isHooked;
    private bool _hasCdp;
    private int _streams;
    private int _active;
    private int _mapped;
    private bool _overlayEnabled = true;
    private bool _selfEnabled = true;

    public string Folder { get; init; } = "";
    public string Exe { get; init; } = "";
    public string ExePath { get; init; } = "";
    public int Port { get; init; }

    public bool IsLive
    {
        get => _isLive;
        set
        {
            if (SetProperty(ref _isLive, value))
            {
                OnPropertyChanged(nameof(StatusText));
                OnPropertyChanged(nameof(ActionText));
            }
        }
    }

    public bool IsRunning
    {
        get => _isRunning;
        set
        {
            if (SetProperty(ref _isRunning, value))
            {
                OnPropertyChanged(nameof(StatusText));
                OnPropertyChanged(nameof(ActionText));
            }
        }
    }

    public bool IsHooked
    {
        get => _isHooked;
        set
        {
            if (SetProperty(ref _isHooked, value))
            {
                OnPropertyChanged(nameof(StatusText));
            }
        }
    }

    public bool HasCdp
    {
        get => _hasCdp;
        set
        {
            if (SetProperty(ref _hasCdp, value))
            {
                OnPropertyChanged(nameof(StatusText));
            }
        }
    }

    public int Streams
    {
        get => _streams;
        set
        {
            if (SetProperty(ref _streams, value))
            {
                OnPropertyChanged(nameof(StatusText));
            }
        }
    }

    public int Active
    {
        get => _active;
        set
        {
            if (SetProperty(ref _active, value))
            {
                OnPropertyChanged(nameof(StatusText));
            }
        }
    }

    public int Mapped
    {
        get => _mapped;
        set
        {
            if (SetProperty(ref _mapped, value))
            {
                OnPropertyChanged(nameof(StatusText));
            }
        }
    }

    public bool OverlayEnabled
    {
        get => _overlayEnabled;
        set => SetProperty(ref _overlayEnabled, value);
    }

    public bool SelfEnabled
    {
        get => _selfEnabled;
        set => SetProperty(ref _selfEnabled, value);
    }

    public string DisplayName => Folder switch
    {
        "DiscordPTB" => "Discord PTB",
        "DiscordCanary" => "Discord Canary",
        "DiscordDevelopment" => "Discord Dev",
        "Discord" => "Discord",
        _ => Folder
    };

    public string ActionText => IsLive ? "Relaunch" : IsRunning ? "Restart w/ port" : "Launch";

    public string StatusText
    {
        get
        {
            if (IsHooked)
            {
                var streamText = Streams == 1 ? "1 stream" : $"{Streams} streams";
                return HasCdp
                    ? $"hooked, names resolving ({Mapped} mapped, {streamText})"
                    : $"hooked, no debug port ({streamText})";
            }

            if (IsLive)
            {
                return "debug port ready";
            }

            if (IsRunning)
            {
                return "running, no debug port";
            }

            return "not running";
        }
    }
}
