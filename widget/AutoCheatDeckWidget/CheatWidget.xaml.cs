using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Microsoft.Gaming.XboxGameBar;
using Windows.ApplicationModel;
using Windows.Data.Json;
using Windows.System;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Navigation;

namespace AutoCheatDeckWidget
{
    public sealed class GameItem
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string Trainer { get; set; }
        public string Detail { get; set; }
    }

    public sealed class CheatItem
    {
        public string Key { get; set; }
        public string Name { get; set; }
        public string Note { get; set; }
        /// <summary>true / false from the running trainer; null when the trainer isn't running.</summary>
        public bool? On { get; set; }
        public string State => On == null ? "–" : (On == true ? "ON" : "OFF");
        public double StateOpacity => On == true ? 1.0 : 0.55;
        public Visibility NoteVisibility => string.IsNullOrEmpty(Note) ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>
    /// Talks to AutoCheatDeck's local API on 127.0.0.1. AutoCheatDeck (with its admin helper) does
    /// the work: starting trainers and switching cheats through the trainer's own toggle buttons.
    /// If AutoCheatDeck isn't running, the widget starts it through the autocheatdeck: link; it
    /// quits on its own a couple of minutes after the widget stops using it.
    /// </summary>
    public sealed partial class CheatWidget : Page
    {
        private const string BaseUrl = "http://127.0.0.1:47120";
        private static readonly HttpClient Http = CreateClient();

        private readonly ObservableCollection<GameItem> games = new ObservableCollection<GameItem>();
        private readonly ObservableCollection<CheatItem> cheats = new ObservableCollection<CheatItem>();
        private GameItem current;
        private XboxGameBarWidget widget;

        public CheatWidget()
        {
            InitializeComponent();
            GamesList.ItemsSource = games;
            CheatsList.ItemsSource = cheats;
        }

        private static HttpClient CreateClient()
        {
            var client = new HttpClient { Timeout = TimeSpan.FromSeconds(90) };
            client.DefaultRequestHeaders.Add("X-AutoCheatDeck", "1");
            return client;
        }

        protected override async void OnNavigatedTo(NavigationEventArgs e)
        {
            widget = e.Parameter as XboxGameBarWidget;
            // Cheats may have been switched in the trainer itself (or with its hotkeys) while the
            // widget was hidden: refresh their state every time it becomes visible again.
            Window.Current.VisibilityChanged += async (sender, args) =>
            {
                if (args.Visible && current != null)
                {
                    await LoadCheatsAsync(current);
                }
            };
            await LoadGamesAsync();
        }

        /// <summary>Open a link from inside Game Bar (the widget's own launcher; Windows.System.Launcher
        /// refuses because a widget isn't a foreground app).</summary>
        private async Task<bool> LaunchUriAsync(Uri uri)
        {
            if (widget != null)
            {
                return await widget.LaunchUriAsync(uri);
            }
            return await Launcher.LaunchUriAsync(uri);
        }

        // ---------- API ----------

        /// <summary>A request to AutoCheatDeck; if it isn't running (it quits a couple of minutes after
        /// the widget goes quiet), start it again and retry once.</summary>
        private async Task<JsonObject> CallAsync(HttpMethod method, string path, JsonObject body = null)
        {
            try
            {
                return await SendAsync(method, path, body);
            }
            catch (HttpRequestException)
            {
                if (!await EnsureAppAsync())
                {
                    throw;
                }
                return await SendAsync(method, path, body);
            }
        }

        private static async Task<JsonObject> SendAsync(HttpMethod method, string path, JsonObject body = null)
        {
            var request = new HttpRequestMessage(method, BaseUrl + path);
            if (body != null)
            {
                request.Content = new StringContent(body.Stringify(), Encoding.UTF8, "application/json");
            }
            using (var response = await Http.SendAsync(request))
            {
                var text = await response.Content.ReadAsStringAsync();
                var json = JsonObject.Parse(string.IsNullOrEmpty(text) ? "{}" : text);
                if (!response.IsSuccessStatusCode)
                {
                    throw new InvalidOperationException(json.GetNamedString("error", response.ReasonPhrase));
                }
                return json;
            }
        }

        /// <summary>Make sure AutoCheatDeck is running, starting it through its link if needed.</summary>
        private async Task<bool> EnsureAppAsync()
        {
            try
            {
                await SendAsync(HttpMethod.Get, "/api/games");
                return true;
            }
            catch (HttpRequestException)
            {
            }
            ShowStatus("Starting AutoCheatDeck...");
            try
            {
                // Our package's full-trust launcher starts AutoCheatDeck (Game Bar won't open links
                // to desktop programs from a widget).
                await FullTrustProcessLauncher.LaunchFullTrustProcessForCurrentAppAsync();
            }
            catch (Exception)
            {
                ShowStatus("Couldn't start AutoCheatDeck. Open it once on the desktop so the widget can find it.");
                return false;
            }
            for (int i = 0; i < 30; i++)
            {
                await Task.Delay(500);
                try
                {
                    await SendAsync(HttpMethod.Get, "/api/games");
                    return true;
                }
                catch (HttpRequestException)
                {
                }
            }
            ShowStatus("AutoCheatDeck didn't start. Open it on the desktop, then reopen this widget.");
            return false;
        }

        private void ShowStatus(string text) => StatusText.Text = text ?? "";

        private void ShowError(Exception e)
        {
            ShowStatus(e is HttpRequestException
                ? "Lost the connection to AutoCheatDeck. Reopen the widget to start it again."
                : e.Message);
        }

        private static string GamePath(GameItem game, string action) =>
            "/api/games/" + Uri.EscapeDataString(game.Id) + "/" + action;

        // ---------- games ----------

        private async Task LoadGamesAsync()
        {
            ShowGameList();
            if (!await EnsureAppAsync())
            {
                return;
            }
            ShowStatus("Loading games...");
            try
            {
                var result = await CallAsync(HttpMethod.Get, "/api/games");
                games.Clear();
                foreach (var value in result.GetNamedArray("games"))
                {
                    var game = value.GetObject();
                    var running = game.GetNamedBoolean("running", false);
                    var updated = game.GetNamedBoolean("gameUpdated", false);
                    games.Add(new GameItem
                    {
                        Id = game.GetNamedString("id"),
                        Name = game.GetNamedString("name"),
                        Trainer = game.GetNamedString("trainer"),
                        Detail = game.GetNamedString("source") + (running ? " · trainer running" : "")
                                 + (updated ? " · game updated: the trainer may need an update" : ""),
                    });
                }
                ShowStatus(games.Count == 0 ? "No trainers yet. Find one in AutoCheatDeck on the desktop." : "");
            }
            catch (Exception e)
            {
                ShowError(e);
            }
        }

        private void ShowGameList()
        {
            current = null;
            TitleText.Text = "AutoCheatDeck";
            BackButton.Visibility = Visibility.Collapsed;
            GameActions.Visibility = Visibility.Collapsed;
            CheatsList.Visibility = Visibility.Collapsed;
            GamesList.Visibility = Visibility.Visible;
        }

        private async void Game_Click(object sender, ItemClickEventArgs e)
        {
            current = (GameItem)e.ClickedItem;
            TitleText.Text = current.Name;
            TrainerText.Text = current.Trainer;
            BackButton.Visibility = Visibility.Visible;
            GameActions.Visibility = Visibility.Visible;
            GamesList.Visibility = Visibility.Collapsed;
            CheatsList.Visibility = Visibility.Visible;
            cheats.Clear();
            await LoadCheatsAsync(current);
        }

        private async void Back_Click(object sender, RoutedEventArgs e) => await LoadGamesAsync();

        // ---------- cheats ----------

        private static CheatItem ToItem(JsonObject cheat, ref string section)
        {
            var notes = new List<string>();
            var cheatSection = cheat.GetNamedString("section", "");
            if (cheatSection != section)
            {
                section = cheatSection;
                notes.Add(section);
            }
            var kind = cheat.GetNamedString("kind", "");
            // GetNamedValue without a default throws when the key is missing (an older AutoCheatDeck).
            var value = cheat.GetNamedValue("value", JsonValue.CreateNullValue());
            var shown = value.ValueType == JsonValueType.String ? value.GetString() : "";
            if (kind.StartsWith("CheatOptionWithInputAdjustment") || kind == "CheatOptionSetComboBox")
            {
                notes.Add((shown != "" ? shown + " · " : "") + "change with Left / Right in the overlay");
            }
            else if (kind.StartsWith("CheatOptionSetValue") || kind.StartsWith("CheatOptionWithInput"))
            {
                notes.Add((shown != "" ? shown + " · " : "") + "type an amount with Y in the overlay");
            }
            var on = cheat.GetNamedValue("on", JsonValue.CreateNullValue());
            return new CheatItem
            {
                Key = cheat.GetNamedString("key"),
                Name = cheat.GetNamedString("name"),
                Note = string.Join(" · ", notes),
                On = on.ValueType == JsonValueType.Boolean ? on.GetBoolean() : (bool?)null,
            };
        }

        /// <summary>Shows the cheats with their live state; false if they haven't been read yet.</summary>
        private async Task<bool> LoadCheatsAsync(GameItem game)
        {
            try
            {
                var result = await CallAsync(HttpMethod.Get, GamePath(game, "cheats"));
                if (game != current)
                {
                    return true;
                }
                if (result.GetNamedValue("cheats").ValueType != JsonValueType.Array)
                {
                    ShowStatus("Launch the trainer to load its cheats.");
                    return false;
                }
                cheats.Clear();
                string section = "";
                bool running = false;
                foreach (var value in result.GetNamedArray("cheats"))
                {
                    var item = ToItem(value.GetObject(), ref section);
                    running |= item.On != null;
                    cheats.Add(item);
                }
                ShowStatus(running
                    ? "Select a cheat (or Show cheats) to open the cheat overlay."
                    : "The trainer isn't running. Launch it to switch cheats.");
                return true;
            }
            catch (Exception e)
            {
                ShowError(e);
                return true;
            }
        }

        private async void Launch_Click(object sender, RoutedEventArgs e)
        {
            var game = current;
            if (game == null)
            {
                return;
            }
            LaunchButton.IsEnabled = false;
            ShowStatus("Starting the trainer...");
            try
            {
                var result = await CallAsync(HttpMethod.Post, GamePath(game, "launch"));
                if (!result.GetNamedBoolean("readingCheats", false))
                {
                    // Cheats already known: the overlay reads their state itself.
                    await OpenOverlayAsync(game);
                    return;
                }
                // First launch: the trainer needs a moment before its cheats can be read.
                ShowStatus("Trainer started. Reading its cheats...");
                for (int i = 0; i < 100 && game == current; i++)
                {
                    await Task.Delay(300);
                    if (await LoadCheatsAsync(game) && HasState())
                    {
                        await OpenOverlayAsync(game);
                        return;
                    }
                }
                ShowStatus("The trainer is taking a while. Try Reload cheats.");
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
            finally
            {
                LaunchButton.IsEnabled = true;
            }
        }

        private bool HasState()
        {
            foreach (var item in cheats)
            {
                if (item.On != null)
                {
                    return true;
                }
            }
            return false;
        }

        private async void Reload_Click(object sender, RoutedEventArgs e)
        {
            var game = current;
            if (game == null)
            {
                return;
            }
            ReloadButton.IsEnabled = false;
            ShowStatus("Reading the trainer's cheats...");
            try
            {
                await CallAsync(HttpMethod.Post, GamePath(game, "read"));
                await LoadCheatsAsync(game);
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
            finally
            {
                ReloadButton.IsEnabled = true;
            }
        }

        // Cheats are switched in AutoCheatDeck's overlay: the trainer takes focus while switching a
        // cheat, which closes Game Bar, so tapping one here opens the overlay instead.
        private async void Cheat_Click(object sender, ItemClickEventArgs e)
        {
            var game = current;
            var cheat = (CheatItem)e.ClickedItem;
            if (game == null)
            {
                return;
            }
            if (cheat.On == null)
            {
                ShowStatus("Launch the trainer first.");
                return;
            }
            await OpenOverlayAsync(game);
        }

        private async void Overlay_Click(object sender, RoutedEventArgs e)
        {
            if (current != null)
            {
                await OpenOverlayAsync(current);
            }
        }

        private async Task OpenOverlayAsync(GameItem game)
        {
            try
            {
                var result = await CallAsync(HttpMethod.Post, GamePath(game, "overlay"));
                ShowStatus(result.GetNamedBoolean("exclusiveFullscreen", false)
                    ? "Cheats are open over the game. It's in exclusive fullscreen, so use touch, or set its "
                      + "Display mode to Borderless to use the controller."
                    : "Cheats are open over the game. B or Esc closes them.");
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
        }
    }
}
