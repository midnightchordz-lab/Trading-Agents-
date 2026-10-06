// Extends app.json. Firebase (Crashlytics + Analytics) is switched on only
// when BOTH platform config files from the Firebase console are present:
//   frontend/google-services.json        (Android app com.emergent.tradeagentapp.kht259)
//   frontend/GoogleService-Info.plist    (iOS app, same bundle id)
// Until then this returns app.json unchanged, so builds keep working — the
// Firebase config plugins throw if their file is missing.
const fs = require("fs");
const path = require("path");

const ANDROID_FILE = "./google-services.json";
const IOS_FILE = "./GoogleService-Info.plist";

module.exports = ({ config, projectRoot }) => {
  const has = (f) => fs.existsSync(path.join(projectRoot, f));
  if (!has(ANDROID_FILE) || !has(IOS_FILE)) return config;

  return {
    ...config,
    android: { ...config.android, googleServicesFile: ANDROID_FILE },
    ios: { ...config.ios, googleServicesFile: IOS_FILE },
    plugins: [
      ...(config.plugins || []),
      "@react-native-firebase/app",
      "@react-native-firebase/crashlytics",
      ["@react-native-firebase/analytics", { ios: { withoutAdIdSupport: true } }],
    ],
  };
};
