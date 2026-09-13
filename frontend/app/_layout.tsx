import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect, useState } from "react";
import { LogBox } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { useIconFonts } from "@/src/hooks/use-icon-fonts";
import { WatchlistProvider } from "@/src/watchlist";
import { AlertsProvider } from "@/src/alerts";
import { initLanguage } from "@/src/i18n";
import { useAuthGate } from "@/src/auth";
import { LoginScreen } from "@/src/components/LoginScreen";

// Login is required before any screen renders — set to false to make it
// optional again without removing the mechanism.
const AUTH_REQUIRED = true;

// Disable logbox errors etc so that users can see the app
// and agent works as expected.
LogBox.ignoreAllLogs(true);

// Keep the native splash visible from cold start until icon fonts register.
// Required because @expo/vector-icons' componentDidMount fallback fires
// Font.loadAsync against a broken vendor path if any <Icon> mounts before
// the family is registered — which throws on Android Expo Go.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [iconsLoaded, iconsError] = useIconFonts();
  const [langReady, setLangReady] = useState(false);
  const [fontsLoaded, fontsError] = useFonts({
    "SpaceGrotesk-Regular": require("../assets/fonts/SpaceGrotesk-Regular.ttf"),
    "SpaceGrotesk-Medium": require("../assets/fonts/SpaceGrotesk-Medium.ttf"),
    "SpaceGrotesk-Bold": require("../assets/fonts/SpaceGrotesk-Bold.ttf"),
    "JetBrainsMono-Regular": require("../assets/fonts/JetBrainsMono-Regular.ttf"),
    "JetBrainsMono-Medium": require("../assets/fonts/JetBrainsMono-Medium.ttf"),
    "JetBrainsMono-Bold": require("../assets/fonts/JetBrainsMono-Bold.ttf"),
  });

  const ready = (iconsLoaded || iconsError) && (fontsLoaded || fontsError) && langReady;
  const { checking: authChecking, user, refresh: refreshAuth } = useAuthGate();

  useEffect(() => {
    initLanguage().finally(() => setLangReady(true));
  }, []);

  useEffect(() => {
    if (ready && !authChecking) {
      SplashScreen.hideAsync();
    }
  }, [ready, authChecking]);

  // If the CDN is unreachable we fall through on error rather than wedging
  // the app — icons will tofu, but the app still boots.
  if (!ready || authChecking) return null;

  if (AUTH_REQUIRED && !user) {
    return (
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <StatusBar style="dark" />
          <LoginScreen onAuthenticated={refreshAuth} />
        </SafeAreaProvider>
      </GestureHandlerRootView>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <KeyboardProvider>
          <WatchlistProvider>
            <AlertsProvider>
              <StatusBar style="light" />
              <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: "#FFFFFF" } }}>
                <Stack.Screen name="(tabs)" />
                <Stack.Screen name="analysis/[id]" />
                <Stack.Screen name="compare" />
              </Stack>
            </AlertsProvider>
          </WatchlistProvider>
        </KeyboardProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
