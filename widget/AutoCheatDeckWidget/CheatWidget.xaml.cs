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
                    games.Add(new GameItem
                    {
                        Id = game.GetNamedString("id"),
                        Name = game.GetNamedString("name"),
                        Trainer = game.GetNamedString("trainer"),
                        Detail = game.GetNamedString("source") + (running ? " · trainer running" : ""),
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
            if (kind == "CheatOptionSetComboBox")
            {
                notes.Add("pick the option in the trainer window");
            }
            else if (kind.StartsWith("CheatOptionSetValue") || kind.StartsWith("CheatOptionWithInput")
                     || cheat.GetNamedBoolean("adjustable", false))
            {
                notes.Add("value set in the trainer window");
            }
            var on = cheat.GetNamedValue("on");
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
                    ? "Select a cheat to switch it on or off."
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
                // The trainer needs a moment before its window (and cheats) can be read.
                ShowStatus(result.GetNamedBoolean("readingCheats", false)
                    ? "Trainer started. Reading its cheats..."
                    : "Trainer started.");
                for (int i = 0; i < 20 && game == current; i++)
                {
                    await Task.Delay(1500);
                    if (await LoadCheatsAsync(game) && HasState())
                    {
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
            try
            {
                var body = new JsonObject { ["name"] = JsonValue.CreateStringValue(cheat.Name) };
                var result = await CallAsync(HttpMethod.Post, GamePath(game, "toggle"), body);
                var on = result.GetNamedBoolean("on");
                var index = cheats.IndexOf(cheat);
                if (index >= 0)
                {
                    // Replace the row so the list redraws its ON/OFF text.
                    cheats[index] = new CheatItem { Key = cheat.Key, Name = cheat.Name, Note = cheat.Note, On = on };
                }
                ShowStatus(cheat.Name + (on ? " is on" : " is off"));
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
        }
    }
}
