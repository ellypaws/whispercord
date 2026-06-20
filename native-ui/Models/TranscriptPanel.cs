using System.Collections.ObjectModel;

namespace DiscordTranscriber.Native.Models;

public sealed class TranscriptPanel : ObservableObject
{
    private int _active;
    private int _finalCount;
    private bool _newestAtTop;

    public string ClientKey { get; init; } = "unknown";
    public string Title { get; init; } = "Unknown";
    public ObservableCollection<TranscriptItem> Items { get; } = new();
    public Dictionary<string, TranscriptItem> CurrentByUser { get; } = new(StringComparer.OrdinalIgnoreCase);

    public int Active
    {
        get => _active;
        set
        {
            if (SetProperty(ref _active, value))
            {
                OnPropertyChanged(nameof(ActiveText));
            }
        }
    }

    public int FinalCount { get => _finalCount; set => SetProperty(ref _finalCount, value); }
    public string ActiveText => Active > 0 ? $"{Active} speaking" : "";

    // Per-card transcript direction; seeded from the global default when the panel is created.
    public bool NewestAtTop { get => _newestAtTop; set => SetProperty(ref _newestAtTop, value); }
}
