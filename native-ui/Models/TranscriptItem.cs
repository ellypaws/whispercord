namespace DiscordTranscriber.Native.Models;

public sealed class TranscriptItem : ObservableObject
{
    private string _name = "";
    private string _text = "";
    private string? _avatar;
    private bool _isFinal;
    private DateTimeOffset _timestamp = DateTimeOffset.Now;

    public string UserId { get; init; } = "";
    public string Client { get; init; } = "unknown";
    public bool IsEvent { get; init; }

    public string Name { get => _name; set => SetProperty(ref _name, value); }
    public string Text { get => _text; set => SetProperty(ref _text, value); }
    public string? Avatar { get => _avatar; set => SetProperty(ref _avatar, value); }

    public bool IsFinal
    {
        get => _isFinal;
        set
        {
            if (SetProperty(ref _isFinal, value))
            {
                OnPropertyChanged(nameof(IsInterim));
            }
        }
    }

    public bool IsInterim => !IsFinal && !IsEvent;
    public DateTimeOffset Timestamp { get => _timestamp; set => SetProperty(ref _timestamp, value); }
    public string TimeText => Timestamp.ToLocalTime().ToString("HH:mm:ss");
}
