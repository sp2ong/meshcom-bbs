# meshcom-bbs

Otwartoźródłowy BBS (Bulletin Board System) dla krótkofalarskiej sieci MeshCom. Działa na Raspberry Pi
(lub dowolnym Linuksie), łączy się z nodem MeshCom przez **KISS/TCP** albo **ext-UDP**, wiadomości trzyma w **SQLite**.
Obsługa jak w BBS DB0SEP-12: komendy wysyła się jako DM na znak BBS.

## STATUS PROJEKTU
Autor nie przewiduje dalszych prac nad projektem. Projekt miał charakter poznawczy więc dalsze prace
nad projektem pozostawiam osobom które chciałby wykorzystywać ten projekt w przyszłości

## Szybki start

Wymaga Pythona 3.11+ (bez zależności zewnętrznych).

```
cp config.example.toml config.toml      # ustaw znak, IP noda, transport
python3 -m mcbbs -c config.toml -v --log mcbbs.log   # --log: kopia logu do pliku (rotacja 1 MB x 5)
```

Na nodzie (konsola lub web UI):

| Transport | Polecenia na nodzie | Uwagi |
|---|---|---|
| KISS/TCP | `--kiss on`, `--kiss tx on` | BBS łączy się DO noda (port 8001). Node przyjmuje **jednego** klienta, więc sonda `mc_probe.py` i BBS nie mogą działać naraz. Tylko ESP32 z WiFi STA. |
| ext-UDP | `--extudp on`, `--extudpip <IP_BBS>` | Węzeł wysyła na port 1799 hosta BBS. Działa też na Ethernet. W configu wystarczy `type = "udp"` i `host`; `port` jest opcjonalny (domyślnie 1799, w obie strony). |

**Zalecane: `call` = znak Twojego noda** (jedna deklaracja; `node_call` jest opcjonalny i potrzebny tylko, gdy BBS
ma inny SSID niż node). Powód i ograniczenia opisuje sekcja „Znak BBS, SSID i routing DM". W skrócie: przez ext-UDP
BBS nie wybiera znaku nadawcy (nadaje pod znakiem noda), a przez KISS node przyjmuje tylko znak o **tym samym
znaku bazowym** co on sam (inaczej odrzuca ramkę kodem 0x02), przy czym SSID nadawcy w KISS to 0–15 (AX.25 ma 4 bity;
sam MeshCom pozwala na 0–99, a adresat w wiadomości może mieć dowolny SSID).

Autostart: `contrib/mcbbs.service` (systemd).

## Komendy

Komendy działają **bez prefiksu** (limit pakietu to ok. 149 znaków i każdy bajt się liczy; w DB0SEP prefiks `db0sep `
zabiera 7). Opcjonalnie można je poprzedzić **`!`** (1 bajt: `!read 2`, `!list`), znakiem BBS albo słowem z `prefixes`
(np. `bbs l`). Na nodzie współdzielonym z ludźmi można wymusić prefiks (`require_prefix = true`; wtedy wystarczy `!`).
Po `!` BBS zawsze odpowiada (także „nieznana komenda"); bez prefiksu na zwykłą rozmowę milczy.

| Komenda | Działanie |
|---|---|
| `add` | opcjonalne: pokazuje powitanie i regulamin (konto powstaje samo, patrz niżej) |
| `h` | pomoc |
| `l` / `l pN` | lista Twoich wiadomości prywatnych: `*[1] ZNAK: temat (data)`, `*` przed numerem = nieprzeczytana; po 5 na stronę (`l p2` = dalsze) |
| `r N` | czytaj wiadomość N |
| `d N` / `d *` | usuń wiadomość N / wszystkie (`e` z DB0SEP działa jako alias) |
| `s ZNAK Temat:Treść END` | wiadomość prywatna do zarejestrowanego użytkownika (komenda `s` jest wymagana) |
| `br` | lista **tematów** ostatnich 5 wpisów tablicy: `[numer] ZNAK: temat (wiek)` |
| `br pN` | strona N listy (`br p2` = starsze 5 wpisów; stopka podpowiada, ile ich jest) |
| `br N` | czytaj pełną treść wpisu `[N]` |
| `bs Temat:Treść END` | wpis na tablicę |
| `u` | zarejestrowani użytkownicy |
| `mh` | ostatnio słyszane stacje |
| `p` / `t` | ping / uptime |

**Konta powstają same.** Pierwsza rozpoznana komenda od nowej stacji (np. `l`, `bl`, `s ...`) zakłada konto (znak
bazowy, bez SSID) i dodaje do odpowiedzi jedną krótką ramkę powitalną z regulaminem (`rules_text`) i informacją, po ilu
godzinach poczta wygasa. Diagnostyka (`p`, `t`, `h`) nikogo nie zapisuje, a zwykła rozmowa i nieznane słowa tym
bardziej. Poczta prywatna działa tylko dla stacji, które już korzystały z BBS. Pojęcia „HOME BBS" z F6FBB nie ma, bo
nie ma jeszcze forwardu między BBS-ami (kolumna `origin` w bazie jest na to przygotowana).

**Treść kończy słowo `END`** (też `end`, `End`; jako osobne słowo na końcu; nie wchodzi do treści). Gdy po
`END` nic nie ma, wiadomość jest zapisywana od razu. Gdy `END` brakuje, BBS **czeka na ciąg dalszy**: kolejne DM-y od
tego nadawcy to zwykły tekst (bez prefiksu i bez `!`), sklejany w jedną wiadomość. Podczas pisania BBS milczy
(każda odpowiedź to dodatkowy airtime). `!end` kończy, `!cancel` (albo `cancel`) anuluje. Gdy ktoś zapomni `END`,
po `draft_autosave_s` (180 s) ciszy BBS **zapisuje to, co ma**, i wysyła jedną informację (`... (autozapis: brak END)`).
Pusty tekst po dwukropku (`bs Temat:`) od razu włącza tryb pisania z krótką instrukcją.
Łączna treść: do `max_body_chars`. Czytanie długiej wiadomości (`r N`, `br N`) BBS dzieli na kilka ramek `(n/m)`.
Szkice są per nadawca (znak z SSID), więc kilka osób może pisać naraz; są tylko w pamięci (restart je kasuje).

Aliasy (pełne nazwy): `help`, `list`, `inbox`, `read`, `delete`, `del`, `e`, `erase`, `send`, `ping`, `uptime`,
`users`, `heard`, `register`, `board`, `bulletins`, `post`.
Numeracja w `l`/`r`/`d` jest względna w skrzynce użytkownika (po usunięciu numery się przesuwają); numer wpisu
tablicy to stały numer wiadomości. Odpowiedzi są po polsku **bez ogonków** (zgodność z małymi wyświetlaczami).

## Shell administracyjny (lokalnie)

```
python3 -m mcbbs -c config.toml --shell --as SP2ONG-7
```

Otwiera tę samą bazę SQLite co działający BBS (tryb WAL, oba procesy mogą działać równolegle) i pozwala używać BBS jak
przez radio (te same komendy, aliasy, `END`, `!`) jako wskazany znak oraz robić administrację:

```
SP2ONG-7@bbs> stats
SP2ONG-7@bbs> users
SP2ONG-7@bbs> purge-posts --days 30
SP2ONG-7@bbs> del-bulletin 7
SP2ONG-7@bbs> del-user SQ9XXX
SP2ONG-7@bbs> queue
SP2ONG-7@bbs> dm SQ9GIN-6 Czesc, tu sysop
```

Wysyłka (`dm`, powiadomienia o nowej poczcie) trafia do kolejki nadawczej w bazie, a nadaje **demon BBS**. Dzięki temu
node nie dostaje drugiego klienta KISS (przyjmuje tylko jednego). Dostęp administratora to dostęp do pliku bazy;
komend administracyjnych po radiu celowo nie ma (radio jest jawne i nie ma tam uwierzytelnienia nadawcy).
Podaj w `--as` swój prawdziwy znak z SSID, żeby powiadomienia o poczcie trafiały na Twój radiotelefon.

## Bramka JS8Call (KF → BBS, etap 1: tylko odbiór)

BBS może odbierać wiadomości z **JS8Call** (API po TCP) i wkładać je do bazy; sam **niczego nie nadaje na KF**.
Włącz w JS8Call serwer API (Settings → Reporting → Network Message API) i dodaj sekcję `[js8call]` (patrz
`config.example.toml`). Bramka **nie potrzebuje własnego znaku**: o rodzaju wpisu decyduje adresat (pole TO),
a treść ma postać `Tytuł : Treść` (podział na pierwszym dwukropku; bez dwukropka tytuł jest pusty):

| Wpisz w JS8Call | Efekt w BBS |
|---|---|
| `@GRM Tytuł : Treść` (`groups`) | wpis na tablicy: `[7] SP9XYZ@JS8: Tytuł (12m)` |
| `@EMC Tytuł : Treść` (`urgent`) | wpis PILNY: `![8] SP1GHK@JS8: EMC: Tytuł (1m)`; przy `urgent_send = true` także wysyłka na grupę MeshCom `urgent_group` (domyślnie 9) |
| `@EMC26024 Tytuł : Treść` | jak wyżej, ale na grupę 26024; **tylko** jeśli numer jest w `urgent_groups`, inaczej wiadomość jest ignorowana w całości |
| `@PM SQ2ABC Tytuł : Treść` (`private`) | poczta prywatna dla użytkownika BBS `SQ2ABC` |

- Wszystko inne słyszane w JS8Call jest ignorowane (w tym DM-y bez `@PM`, HEARTBEAT, SNR, grupy spoza listy).
- **Wysyłka EMC na grupę MeshCom** (`urgent_send`, domyślnie wyłączona): tekst `EMC: SP1GHK@JS8 Tytuł: Treść`, ramki
  do 147 B (i `160 − 2 − długość numeru grupy`), **bez ACK i bez ponawiania** (grupy nie mają potwierdzeń);
  `urgent_repeat` (domyślnie 1) wysyła kopie co 2 min. `urgent_max_per_hour` (6) chroni LoRa: po przekroczeniu
  wpis zostaje na tablicy, ale nic nie jest nadawane. Numer grupy MeshCom ma maks. 5 cyfr.
- Nadawca z KF **nie jest uwierzytelniony**, dlatego wpisy mają dopisek `@JS8`. Każdy, kto usłyszy Twoją stację,
  może napisać `@EMC`: włączaj `urgent_send` świadomie. Zaobserwowane przez UDP: wiadomość na grupę
  numeryczną (np. 112) trafia przez bramę noda do sieci i na dashboard `sp.meshcom.pl`, a plain `@EMC` (grupa 9) nie
  pojawia się na dashboardzie, czyli zostaje lokalnie w zasięgu radiowym noda (do potwierdzenia odbiorem na innej stacji).
  Dlatego `urgent_groups` to świadoma zgoda na wyjście poza lokalny zasięg.
- `urgent_dm = true` (domyślnie wyłączone) dodatkowo wysyła DM `PILNE EMC od ZNAK. Czytaj: br N` do osób z `urgent on`;
  to ma sens tylko na małych instalacjach (na grupę 9 wiadomość i tak słyszą wszyscy).
- Test bez radia: `tools/js8_fake.py` (atrapa API JS8Call). JS8Call pisze wszystko wielkimi literami; pole „Direct
  Callsign" może być puste lub wypełnione, wynik jest ten sam.
- Czego jeszcze **nie ma**: odpowiedzi przez JS8 (wymaga sprawdzenia formatu `TX.SEND_MESSAGE` na żywo) oraz MeshCom → KF.

## Pogoda / telemetria noda: `wx`

`wx` (aliasy `weather`, `pogoda`) pokazuje ostatnią telemetrię **własnego noda**, np.
`TELEMETRIA (12m temu): T 18.5C | H 48% | QFE 1022.7 | QNH 1031.1 hPa`. Źródło: ramki UDP `tele` (liczba całkowita `0` =
brak czujnika, liczba z przecinkiem = odczyt) albo komentarz pozycji APRS przez KISS (`T=`, `H=`, `P=`, `Q=`; zera
pomijane, bo tam nie da się odróżnić braku czujnika). Pola z różnych ramek są łączone. Brak danych:
`TELEMETRIA: brak danych`; starsze niż 2 h oznaczone `NIEAKTUALNE`. Komenda nie rejestruje użytkownika.

## Jak to działa

```
Node MeshCom ── KISS/TCP lub ext-UDP ──> Transport ──> BBS (service) ──> Commands ──> Store (SQLite)
                                           ^                │
                                           └── kolejka nadawcza (outbox): pacing, ACK, ponawianie
```

- **Transport** to wymienialny interfejs: `send(dst, text) -> klucz`, wywołania zwrotne `on_message`, `on_ack`,
  `on_tx_result`, `on_heard`. Reszta kodu nie wie, czy to KISS czy UDP.
- **Zawodna sieć:** wiadomość jest „dostarczona" dopiero po ACK-u. Bez ACK-a BBS ponawia (`retry_delays_s`),
  a po `reply_attempts` próbach rezygnuje. Serwer MeshCom bywa zawodny, więc to wymóg, nie dodatek.
- **Deduplikacja:** nadawca, który nie dostał ACK-a, ponawia wiadomość. BBS wysyła ACK ponownie, ale komendy nie
  wykonuje drugi raz (klucz: znak nadawcy + `{nn`, okno `dedupe_window_s`).
- **Pacing:** jedna ramka na `pace_s` sekund (airtime LoRa), limit `max_replies_per_hour` na nadawcę.
- **Długość:** odpowiedzi dzielone na ramki ≤ `frame_bytes` **bajtów** UTF-8 (nie znaków!) z numeracją `(n/m)`.
- **Echa własnych wysyłek** (od znaku noda i BBS) są ignorowane, inaczej BBS przetwarzałby własne odpowiedzi.
- **Prawdziwy znak nadawcy** (SSID -16…-99) bierze z ramki SrcInfo (0x20), nie z zaciśniętego `-15`.
- **Retencja** (liczona od zapisu, sprawdzana w każdym ticku, więc wiadomość znika od razu po terminie): tablica
  `retention_bulletin_hours` (domyślnie 720 h = 30 dni), poczta prywatna `retention_private_hours` (168 h = 7 dni,
  także nieprzeczytana). Przypomnienie o poczcie co `notify_interval_h`.
- **Powiadomienia o nowej poczcie** idą na wszystkie pełne adresy (SSID), z których użytkownik pisał w ostatnich
  `notify_addr_days` (7) dniach, najwyżej `notify_max_addrs` (3) najświeższe: jedno urządzenie na półce, drugie w terenie.
  Poczta jest na znak bazowy, więc czytać ją można z każdego SSID. `notify_all_ssids = false` wraca do jednego adresu
  (ostatnio użytego). Odstęp między powiadomieniami: `notify_min_gap_s`.
- Tabela `messages` ma kolumnę `origin`, więc przyszła federacja BBS-ów nie wymaga zmiany schematu.

## Znak BBS, SSID i routing DM (ustalenia z kodu firmware MeshCom, gałąź `dev`)

- **Sieć radiowa MeshCom nie ma routingu**, tylko flooding z licznikiem hopów
  (`docs/prio-talk-flood-networking.md`: „keine Routing-Tabelle, keine Next-Hop-Auswahl"). Nie ma „rejestracji" znaku
  w sieci; stacja istnieje, bo ją słychać (beacony, wiadomości).
- **Node uznaje DM za „do siebie" po dokładnym porównaniu ciągu znaków ze swoim znakiem, razem z SSID**
  (`src/lora_functions.cpp`, `strcmp(destination_call, meshcom_settings.node_call)`). DM do `SP2ONG-12` nie jest
  przetwarzany przez node `SP2ONG-1` jako własny: traktuje go jak cudzy ruch (przekazuje dalej). Klient KISS/UDP widzi
  jednak każdą wiadomość, którą node odebrał, więc BBS pod innym SSID dostałby te DM-y, **jeśli node je dostanie**.
- **Node sam potwierdza (ACK) DM do swojego dokładnego znaku**, zarówno z RF, jak i z serwera, z deduplikacją po
  (nadawca, `{NNN`) i ograniczonym ponownym ACK-iem. Dlatego BBS pod znakiem noda **nie wysyła własnych ACK-ów**
  (`ack_incoming` w trybie auto = false); BBS pod innym SSID musi je wysyłać sam (KISS: ramka `:ZNAK :ackNN`).
- **Serwer → bramka:** bramka wypromieniowuje na RF każdą wiadomość tekstową od serwera, bez filtra „adresat znany
  tutaj" (`docs/pn-zustellung-dedup.md` §2.4). **Czy serwer wysyła do danej bramki DM adresowany do znaku X, jest w
  dokumentacji firmware oznaczone jako „offen" (otwarte)**, bo kod serwera nie jest publiczny. To jest powód, dla
  którego nie wiadomo, czy BBS pod innym SSID dostanie DM-y przez internet.
- **Pozycja jako „beacon" BBS:** przez KISS można wstrzyknąć ramkę pozycji `!…` pod znakiem BBS (ten sam znak bazowy co
  node, SSID 0–15). Firmware nada ją na LoRa, a bramka z internetem prześle ją do serwera
  (`sendInjectedPosition` w `src/loop_functions.cpp`), więc BBS może pojawić się na mapie. To **nie jest rejestracja
  routingu** – tylko zwykła pozycja; czy serwer na jej podstawie kieruje DM-y, nie wiadomo (j.w.).
- **KISS nie jest bramką dla cudzych znaków.** Ramki z innym znakiem bazowym niż node są odrzucane (kod 0x02); dokumentacja
  mówi wprost, że nie ma trybu „gateway", który przekazywałby dowolne znaki. Most KISS↔HF (VARA/Mercury) mógłby więc
  najwyżej nadawać pod znakiem noda. Wstrzyknięta ramka jest traktowana jak własna transmisja noda (nie tworzy wpisu
  mheard ani trasy).


## Testy

```
python3 -m unittest discover -s . -t . -v
```

Testy transportów używają prawdziwych JSON-ów i ramek zarejestrowanych na nodzie SP2ONG-1; test integracyjny
stawia atrapę noda na localhost i sprawdza pełny obieg DM → ACK → odpowiedź → ACK odpowiedzi.

## Relacja do innych projektów

Istniejący BBS DL4QB (DB0SEP-12) to skrypt powłoki na ext-UDP z systemową pocztą Linuksa; jest na GPL.
Ten projekt **nie kopiuje jego kodu** – zgodność dotyczy zestawu komend. Cel to przenośna, testowalna implementacja
(SQLite, KISS i UDP, ponawianie, deduplikacja), którą można postawić lokalnie.

## Licencja

meshcom-bbs, Copyright (C) 2026 SP2ONG.

Ten program jest wolnym oprogramowaniem: możesz go rozpowszechniać i/lub modyfikować na warunkach GNU General Public
License, wydanej przez Free Software Foundation, w wersji 3 albo (według Twojego wyboru) dowolnej późniejszej.
Program jest rozpowszechniany w nadziei, że będzie użyteczny, ale **bez jakiejkolwiek gwarancji**. Pełny tekst licencji
jest w pliku [LICENSE](LICENSE).
