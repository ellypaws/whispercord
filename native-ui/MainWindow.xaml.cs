using CommunityToolkit.WinUI.Controls;
using DiscordTranscriber.Native.Models;
using DiscordTranscriber.Native.ViewModels;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;

namespace DiscordTranscriber.Native;

public sealed partial class MainWindow : Window
{
    private readonly MainViewModel _viewModel;
    private bool _ready;

    public MainWindow()
    {
        InitializeComponent();
        Title = "Discord Live Transcriber";
        TryApplyBackdrop();
        ConfigureWindow();

        _viewModel = new MainViewModel(DispatcherQueue.GetForCurrentThread());
        Root.DataContext = _viewModel;
        Closed += (_, _) => _viewModel.Dispose();
    }

    private void ConfigureWindow()
    {
        try
        {
            AppWindow.Resize(new Windows.Graphics.SizeInt32(1220, 820));
            AppWindow.SetIcon("Assets\\app.ico");
        }
        catch
        {
            // AppWindow APIs require a recent Windows build; fall back to defaults.
        }
    }

    private async void Root_Loaded(object sender, RoutedEventArgs e)
    {
        await _viewModel.InitializeAsync();
        _ready = true;
    }

    private void TryApplyBackdrop()
    {
        try
        {
            SystemBackdrop = new MicaBackdrop();
        }
        catch
        {
            // Older Windows builds fall back to normal themed brushes.
        }
    }

    private void Nav_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        var tag = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString() ?? "live";
        LiveView.Visibility = tag == "live" ? Visibility.Visible : Visibility.Collapsed;
        SettingsView.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
        ConsoleView.Visibility = tag == "console" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void Start_Click(object sender, RoutedEventArgs e)
    {
        await _viewModel.StartBackendAsync();
    }

    private async void Stop_Click(object sender, RoutedEventArgs e)
    {
        await _viewModel.StopBackendAsync();
    }

    private async void Restart_Click(object sender, RoutedEventArgs e)
    {
        await _viewModel.RestartBackendAsync();
    }

    private async void RefreshClients_Click(object sender, RoutedEventArgs e)
    {
        await _viewModel.RefreshClientsAsync();
    }

    private async void SaveSettings_Click(object sender, RoutedEventArgs e)
    {
        await _viewModel.SaveSettingsAsync();
    }

    private async void ClientAction_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is DiscordClientInfo client)
        {
            await _viewModel.EnsureClientAsync(client);
        }
    }

    private void ClientSetting_Toggled(object sender, RoutedEventArgs e)
    {
        if (_ready)
        {
            _viewModel.RestartNeeded = true;
        }
    }

    private void ClearPanel_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is TranscriptPanel panel)
        {
            _viewModel.ClearPanel(panel);
        }
    }

    private void FlipPanel_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is TranscriptPanel panel)
        {
            _viewModel.FlipPanelDirection(panel);
        }
    }

    private void Keywords_TokenItemAdding(TokenizingTextBox sender, TokenItemAddingEventArgs args)
    {
        args.Item = args.TokenText.Trim();
    }

    private void ClearConsole_Click(object sender, RoutedEventArgs e)
    {
        _viewModel.ClearConsole();
    }

    private void CopyConsole_Click(object sender, RoutedEventArgs e)
    {
        var package = new DataPackage();
        package.SetText(string.Join(Environment.NewLine, _viewModel.Logs));
        Clipboard.SetContent(package);
    }
}
