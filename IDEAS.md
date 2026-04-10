# 💡 Feature Ideas for Scripture & Skies Dashboard

## 🕐 Time & Date
- [ ] **World clocks** — Show time in multiple cities (Tokyo, London, NYC, etc.)
- [ ] **Countdown timer** — Days until next holiday, birthday, or custom event
- [x] **This day in history** — What happened on this date in past years
- [ ] **Moon phase** — Current lunar phase with emoji 🌑🌒🌓🌔🌕
- [ ] **Sunrise/sunset times** — Using `sunrise-sunset.org` API

## 🌤️ Weather & Environment
- [ ] **Air quality index (AQI)** — From `aqicn.org` or `openweathermap.org/api/air-pollution`
- [ ] **UV index** — Current UV level with safety advice
- [ ] **Pollen count** — Allergy info for your area
- [ ] **Outdoor weather forecast** — 5-day forecast from OpenWeatherMap (complement indoor sensor)
- [ ] **Indoor vs outdoor temp comparison** — Side-by-side gauge

## 📊 Data & Charts
- [ ] **Humidity & pressure history charts** — Already stored in DB but not displayed
- [ ] **Speedtest history chart** — Download/upload trends over time
- [ ] **CPU temperature chart** — Track Pi thermals over time
- [ ] **Min/max/avg stats** — Daily temperature summary cards

## 🎮 Fun & Entertainment
- [ ] **Riddle / brain teaser of the day** — From a trivia API
- [ ] **Random joke** — `https://official-joke-api.appspot.com/random_joke`
- [ ] **NASA Astronomy Picture of the Day** — `api.nasa.gov/planetary/apod`
- [ ] **Random fun fact** — `https://uselessfacts.jsph.pl/api/v2/facts/random`
- [ ] **Spotify "Now Playing"** — If Spotify is used on the Pi
- [ ] **Random recipe** — From TheMealDB (`themealdb.com/api.php`)
- [ ] **Random cat/dog photo** — `thecatapi.com` / `dog.ceo/api`

## 📚 Learning & Productivity
- [x] **Algorithm of the day** — Rotate through sorting, graph, DP algorithms with pseudocode
- [ ] **LeetCode daily challenge** — Link to the daily problem
- [ ] **Language learning word** — Random word in Spanish/Japanese/etc.
- [ ] **Math puzzle** — Simple daily math challenge
- [ ] **Keyboard shortcut tip** — Vim, VS Code, Linux shortcuts
- [ ] **Design pattern of the day** — Rotate through GoF patterns

## 🏥 Health & Lifestyle
- [ ] **Pomodoro timer** — Interactive work/break timer
- [ ] **Water reminder** — "Drink water!" every N minutes
- [ ] **Step/exercise counter** — If connected to a fitness API
- [ ] **Daily affirmation** — Motivational message

## 🌐 Tech & Dev
- [ ] **GitHub contribution graph** — Your commit activity
- [x] **Hacker News top stories** — `https://hacker-news.firebaseio.com/v0/topstories.json`
- [ ] **Product Hunt trending** — Today's hot products
- [ ] **Stack Overflow hot questions** — From the SO API
- [ ] **Is it down?** — Monitor status of services you care about (ping check)
- [ ] **Pi-hole stats** — If running Pi-hole, show blocked queries

## 🗺️ Location & Travel
- [ ] **ISS tracker** — Current position of the International Space Station (`api.open-notify.org`)
- [ ] **Currency exchange rates** — USD to EUR, GBP, JPY, etc.
- [ ] **Gas prices** — Local fuel prices
- [ ] **Traffic conditions** — Commute time estimate

## 🎨 Visual Widgets
- [ ] **Live analog clock** — CSS/JS animated clock
- [ ] **Color of the day** — Random color with hex code
- [ ] **Motivational wallpaper** — Rotate background images
- [ ] **Mini calendar** — Current month with today highlighted
- [ ] **Weather-based theme** — Auto dark/light mode based on time of day

---

## 🔧 Code Improvements
- [ ] **Global error handler** — Return JSON for all unhandled exceptions
- [ ] **DB context manager** — Replace manual open/close with `with` pattern
- [ ] **Caching layer** — TTL cache for external API calls (news, quotes, etc.)
- [ ] **Config via env vars** — Timezone, country code, port, API keys
- [ ] **Rate-limit `/api/speedtest`** — Prevent spamming background threads
- [ ] **Move `capture_image()` out of `/api/data`** — Make it async/non-blocking
- [ ] **Move scattered imports to top** — `json`, `xml.etree.ElementTree`
- [ ] **Health check endpoint** — `/api/health`
- [ ] **API index endpoint** — `/api` listing all routes
- [ ] **Speedtest history endpoint** — `/api/speedtest-history`
- [ ] **Structured logging** — Consistent format with timestamps

---

## ⭐ Quick Wins (Easy + High Impact)

| Widget | API | Effort |
|--------|-----|--------|
| 🌙 Moon phase | `weather.gov` or calculate locally | Small |
| 🃏 Random joke | `official-joke-api.appspot.com` | Tiny |
| 🚀 NASA APOD | `api.nasa.gov/planetary/apod` | Small |
| 💡 Fun fact | `uselessfacts.jsph.pl` | Tiny |
| 📰 Hacker News | `hacker-news.firebaseio.com` | Small |
