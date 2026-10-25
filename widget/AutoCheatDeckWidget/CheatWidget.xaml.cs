using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Windows.Data.Json;
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
        public Visibility NoteVisibility => string.IsNullOrEmpty(Note) ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>
    /// Talks to AutoCheatDeck's local API on 127.0.0.1 (the desktop app does the work: launching
    /// trainers runs them as admin, and pressing cheats sends their hotkeys).
    /// </summary>
    public sealed partial class CheatWidget : Page
    {
        private const string BaseUrl = "http://127.0.0.1:47120";
        private static readonly HttpClient Http = CreateClient();

        private readonly ObservableCollection<GameItem> games = new ObservableCollection<GameItem>();
        private readonly ObservableCollection<CheatItem> cheats = new ObservableCollection<CheatItem>();
        private GameItem current;

        public CheatWidget()
        {
            InitializeComponent();
            GamesList.ItemsSource = games;
            CheatsList.ItemsSource = cheats;
        }

        private static HttpClient CreateClient()
        {
            var client = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
            client.DefaultRequestHeaders.Add("X-AutoCheatDeck", "1");
            return client;
        }

        protected override async void OnNavigatedTo(NavigationEventArgs e)
        {
            await LoadGamesAsync();
        }

        // ---------- API ----------

        private static async Task<JsonObject> CallAsync(HttpMethod method, string path, JsonObject body = null)
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

        private void ShowStatus(string text) => StatusText.Text = text ?? "";

        private void ShowError(Exception e)
        {
            ShowStatus(e is HttpRequestException
                ? "Can't reach AutoCheatDeck. Start it on the desktop (it can stay in the tray), then reopen this widget."
                : e.Message);
        }

        // ---------- games ----------

        private async Task LoadGamesAsync()
        {
            ShowGameList();
            ShowStatus("Loading games...");
            try
            {
                var result = await CallAsync(HttpMethod.Get, "/api/games");
                games.Clear();
                foreach (var value in result.GetNamedArray("games"))
                {
                    var game = value.GetObject();
                    games.Add(new GameItem
                    {
                        Id = game.GetNamedString("id"),
                        Name = game.GetNamedString("name"),
                        Trainer = game.GetNamedString("trainer"),
                        Detail = game.GetNamedString("source") + (game.GetNamedBoolean("hasCheats") ? " · cheats ready" : ""),
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

        /// <summary>Shows the cached cheats; false if AutoCheatDeck hasn't read them yet.</summary>
        private async Task<bool> LoadCheatsAsync(GameItem game)
        {
            try
            {
                var result = await CallAsync(HttpMethod.Get, "/api/games/" + Uri.EscapeDataString(game.Id) + "/cheats");
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
                foreach (var value in result.GetNamedArray("cheats"))
                {
                    var cheat = value.GetObject();
                    var notes = new List<string>();
                    var cheatSection = cheat.GetNamedString("section", "");
                    if (cheatSection != section)
                    {
                        section = cheatSection;
                        notes.Add(section);
                    }
                    if (cheat.GetNamedBoolean("adjustable", false))
                    {
                        notes.Add("value set in the trainer window");
                    }
                    cheats.Add(new CheatItem
                    {
                        Key = cheat.GetNamedString("key"),
                        Name = cheat.GetNamedString("name"),
                        Note = string.Join(" · ", notes),
                    });
                }
                ShowStatus("Select a cheat to toggle it (start the game first).");
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
            try
            {
                var result = await CallAsync(HttpMethod.Post, "/api/games/" + Uri.EscapeDataString(game.Id) + "/launch");
                if (!result.GetNamedBoolean("readingCheats", false))
                {
                    ShowStatus("Trainer started.");
                    await LoadCheatsAsync(game);
                    return;
                }
                // AutoCheatDeck reads the new trainer's cheats once its window appears (bounded wait).
                ShowStatus("Trainer started. Reading its cheats...");
                for (int i = 0; i < 20 && game == current; i++)
                {
                    await Task.Delay(2000);
                    if (await LoadCheatsAsync(game))
                    {
                        return;
                    }
                }
                ShowStatus("Couldn't read the cheats yet. Try Reload cheats.");
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
                await CallAsync(HttpMethod.Post, "/api/games/" + Uri.EscapeDataString(game.Id) + "/read");
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
            var cheat = (CheatItem)e.ClickedItem;
            try
            {
                var body = new JsonObject { ["key"] = JsonValue.CreateStringValue(cheat.Key) };
                await CallAsync(HttpMethod.Post, "/api/press", body);
                ShowStatus("Sent " + cheat.Key + ": " + cheat.Name);
            }
            catch (Exception ex)
            {
                ShowError(ex);
            }
        }
    }
}
