# KiranaSaathi Mobile

Native Android/iOS companion app. It is isolated in `mobile/` and does not share or modify the web frontend.

## Development

1. Copy `.env.example` to `.env` and confirm `EXPO_PUBLIC_API_URL`.
2. Run `npm install` inside `mobile/`.
3. Voice recognition requires native modules, so create a development build with `npx expo run:android` or `npx expo run:ios`. It is not supported by standard Expo Go.
4. Start Metro with `npm start`.

## Production builds

Configure an Expo/EAS project, then run `npm run build:android` or `npm run build:ios`.

The app stores JWTs only in the OS encrypted credential store. Images are uploaded directly to the existing FastAPI service and always require a user review before confirmation.
