## 1.0.0 (2025-11-14)


### Features

* initial release of Elli Charger Home Assistant integration ([f86837a](https://github.com/marcszy91/hacs-elli-charger/commit/f86837aa4c1a9708f9a1a6a72cdda632c855c8ee))

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-30

Fork of mawiak/hacs-elli-charger, maintained at kegelmeier/hacs-elli-charger.

### Changed
- Login through the browser (OAuth2 with PKCE, `elli-client` 1.5.0), since Elli's login page blocks email/password logins. Only the refresh token is stored; rotated tokens are saved, and a revoked one starts Home Assistant's reauthentication.
- Entries from 1.0.0 ask to reauthenticate once and keep their entities.
- Status states are now `idle` / `connected` / `charging` (translated).
- All sensors of a wallbox belong to one device, with model, serial and firmware.

### Added
- Charging power sensor (W).
- Total energy sensor for the Energy dashboard: completed charging records plus the running session, never decreasing.
- Polling interval option (2–60 min, default 5).
- German translation.

### Fixed
- A 404 from the charging-sessions endpoint no longer breaks setup; it means nothing is charging.
- The API client is created off the event loop (blocking-call warning).
- Session energy keeps the last session's value instead of going unknown between sessions. It no longer has a state class (long-term statistics); use Total energy for the Energy dashboard.

## [1.0.0] - 2025-11-13

### Added
- Initial release of Elli Charger HACS integration
- authentication with Elli Account
- Station-based sensor architecture supporting multiple wallboxes
- Wallbox status sensor (Idle/Connected/Charging)
- Last session sensor with comprehensive attributes
- Session energy sensor optimized for Home Assistant Energy Dashboard
- Firmware version display in wallbox attributes
- Automatic session tracking with energy consumption
- RFID card tracking for billing
- Support for momentary power monitoring
- Lifecycle and charging state tracking

### Features
- Three sensors per charging station
- Energy consumption tracking in kWh
- Real-time power monitoring in Watts
- Session state and lifecycle tracking
- RFID card identification
- Authentication method tracking
- Firmware version monitoring
- Automatic token refresh
