// AutoCheatDeck widget launcher: the widget's full-trust process.
//
// Game Bar won't let a widget open links to ordinary desktop programs, but a widget may start a
// full-trust process from its own package. This one starts AutoCheatDeck in widget mode, using the
// command AutoCheatDeck registers for its autocheatdeck: link (kept up to date by every start of
// the portable app, so it follows the folder wherever it is), then exits.
//
// Built with the .NET Framework compiler that ships with Windows (C# 5):
//   %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /target:winexe /out:AcdLauncher.exe AcdLauncher.cs
using System;
using System.Diagnostics;
using System.IO;
using Microsoft.Win32;

static class AcdLauncher
{
    // This process runs inside the widget's package, with a private registry and a redirected
    // AppData, so it can't see the autocheatdeck: link registration. AutoCheatDeck also writes the
    // same command to a file in the profile folder, which packaged processes see as-is.
    static string ReadCommand()
    {
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                                   ".autocheatdeck", "widget-command.txt");
        try
        {
            return File.Exists(path) ? File.ReadAllText(path).Trim() : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    // Packaged processes see AppData redirected into the package; the profile folder itself isn't.
    static void Log(string text)
    {
        try
        {
            string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                                       "AutoCheatDeck-launcher.log");
            File.AppendAllText(path, DateTime.Now.ToString("HH:mm:ss ") + text + Environment.NewLine);
        }
        catch (Exception)
        {
        }
    }

    static int Main()
    {
        int code = Run();
        Log("exit " + code);
        return code;
    }

    static int Run()
    {
        string command = ReadCommand();
        Log("registered command: " + (command ?? "(none)"));
        if (string.IsNullOrEmpty(command))
        {
            return 1;  // AutoCheatDeck has never run on this PC
        }

        // The command looks like: "C:\...\AutoCheatDeck.exe" --widget "%1"
        string exe, arguments;
        if (command.StartsWith("\""))
        {
            int end = command.IndexOf('"', 1);
            if (end < 0)
            {
                return 2;
            }
            exe = command.Substring(1, end - 1);
            arguments = command.Substring(end + 1).Trim();
        }
        else
        {
            int space = command.IndexOf(' ');
            exe = space < 0 ? command : command.Substring(0, space);
            arguments = space < 0 ? "" : command.Substring(space + 1).Trim();
        }
        arguments = arguments.Replace("%1", "autocheatdeck:widget");

        ProcessStartInfo info = new ProcessStartInfo(exe, arguments);
        info.UseShellExecute = false;
        info.WorkingDirectory = Path.GetDirectoryName(exe);
        try
        {
            Process started = Process.Start(info);
            Log("started " + exe + " " + arguments + " (pid " + started.Id + ")");
            return 0;
        }
        catch (Exception e)
        {
            Log("start failed: " + e.Message);
            return 3;
        }
    }
}
