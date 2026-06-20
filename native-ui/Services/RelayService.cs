using System.Net.WebSockets;
using System.Text;
using System.Text.Json.Nodes;

namespace DiscordTranscriber.Native.Services;

public sealed class RelayService : IDisposable
{
    private CancellationTokenSource? _cts;

    public event Action<string>? StateChanged;
    public event Action<JsonObject>? MessageReceived;

    public Task StartAsync(int port)
    {
        Stop();
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => RunAsync(port, _cts.Token));
        return Task.CompletedTask;
    }

    public void Stop()
    {
        if (_cts is null)
        {
            return;
        }

        _cts.Cancel();
        _cts.Dispose();
        _cts = null;
        StateChanged?.Invoke("relay off");
    }

    private async Task RunAsync(int port, CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            using var socket = new ClientWebSocket();
            try
            {
                StateChanged?.Invoke("connecting");
                await socket.ConnectAsync(new Uri($"ws://127.0.0.1:{port}"), token).ConfigureAwait(false);
                StateChanged?.Invoke("relay");
                await ReadLoopAsync(socket, token).ConfigureAwait(false);
            }
            catch when (token.IsCancellationRequested)
            {
                break;
            }
            catch
            {
                StateChanged?.Invoke("relay off");
                try
                {
                    await Task.Delay(2000, token).ConfigureAwait(false);
                }
                catch
                {
                    break;
                }
            }
        }
    }

    private async Task ReadLoopAsync(ClientWebSocket socket, CancellationToken token)
    {
        var buffer = new byte[16 * 1024];

        while (!token.IsCancellationRequested && socket.State == WebSocketState.Open)
        {
            using var ms = new MemoryStream();
            WebSocketReceiveResult result;
            do
            {
                result = await socket.ReceiveAsync(buffer, token).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    return;
                }

                ms.Write(buffer, 0, result.Count);
            }
            while (!result.EndOfMessage);

            var text = Encoding.UTF8.GetString(ms.ToArray());
            if (JsonNode.Parse(text) is JsonObject obj)
            {
                MessageReceived?.Invoke(obj);
            }
        }
    }

    public void Dispose()
    {
        Stop();
    }
}
