// Extends app.json. Firebase (Crashlytics + Analytics) is switched on per
// platform, by the config file from the Firebase console being present:
//   frontend/google-services.json        -> Android (com.emergent.tradeagentapp.kht259)
//   frontend/GoogleService-Info.plist    -> iOS (same bundle id)
// Without a platform's file that platform builds exactly as before — the
// Firebase iOS config plugin throws if its file is missing.
const fs = require("fs");
const path = require("path");

const ANDROID_FILE = "./google-services.json";
const IOS_FILE = "./GoogleService-Info.plist";

module.exports = ({ config, projectRoot }) => {
  const has = (f) => fs.existsSync(path.join(projectRoot, f));
  const android = has(ANDROID_FILE);
  const ios = has(IOS_FILE);
  if (!android && !ios) return config;

  const plugins = [...(config.plugins || [])];
  if (ios) {
    // Its iOS half adds FirebaseApp.configure() and the plist; its Android half
    // duplicates what Expo already does for android.googleServicesFile.
    plugins.push("@react-native-firebase/app");
    plugins.push(["@react-native-firebase/analytics", { ios: { withoutAdIdSupport: true } }]);
  }
  if (android) {
    // Expo itself copies google-services.json and applies the Google Services
    // Gradle plugin when android.googleServicesFile is set; this adds the
    // Crashlytics Gradle plugin (Android-only).
    plugins.push("@react-native-firebase/crashlytics");
  }

  return {
    ...config,
    android: android ? { ...config.android, googleServicesFile: ANDROID_FILE } : config.android,
    ios: ios ? { ...config.ios, googleServicesFile: IOS_FILE } : config.ios,
    plugins,
  };
};

