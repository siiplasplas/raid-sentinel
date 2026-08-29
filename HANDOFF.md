# Devir notu

Bu dosya, projeyi devralan bir sonraki oturum için. Kodda görünmeyen
kararları, sahada öğrenilenleri ve doğrulanmamış varsayımları içerir.

**Proje:** `C:\Users\Admin\Desktop\Projeler\raid-sentinel`
**Kullanıcı:** Halit — Türkçe konuşuyor, Rust oynuyor, sistemi kendi
makinesinde çalıştırıyor.
**Son durum:** 27 Ağustos 2026 · 158 test · lint temiz · sahada çalışıyor

---

## Ne yapıyor

Rust'ta üs raid yediğinde haber veren, kendi kendine barındırılan erken
uyarı sistemi. Rust+ Companion API'sinden alarm sinyalleri alır, tehdit
puanlar, Discord/ntfy'ye yazar, ciddi durumda telefon zinciriyle arar ve
tool cupboard'a kalan süreyi tahmin eder.

**Kritik kavrayış:** Değer, "alarmı iletmekte" değil, **yanlış alarmı
elemekte ve ne kadar vakit kaldığını söylemekte.** Bir raidde saniyeler
içinde onlarca tetikleme gelir; hepsini bildirmek kanalı okunmaz yapar ve
telefon faturasını patlatır.

---

## Sahada doğrulanmış durum

Bunlar gerçek bir sunucuda (`[EU/TR] Suayip 3x`) canlı test edildi:

| Parça | Durum |
|---|---|
| FCM kaydı + Google bağlantısı | ✅ push 2 sn'de geliyor |
| Facepunch push kaydı | ✅ HTTP 200 |
| Sunucu eşleştirme | ✅ `93.113.57.181:28082` |
| WebSocket + entity aboneliği | ✅ bağlı, izliyor |
| Alarm → olay → Discord | ✅ uçtan uca çalıştı |
| ETA hesabı | ✅ üs tanımıyla çalışıyor |
| Telefon araması | ❌ **hiç denenmedi** |

**Kullanıcının kurulumu:** tek HBHF Sensor + tek Smart Alarm
(`entity_id 22608211`, bölge `maintc`, kademe elle 3, tür `presence`).
Üs tanımı: `maintc → TC`, 1 taş duvar. Kanal: Discord.

---

## Bugün sahada bulunan altı hata

Hiçbiri simülasyonla yakalanamazdı. Düzeltmeleri geri alma.

**1. Eşleştirme akışı eskiydi.** Ekosistemdeki bütün araçlar (liamcottle
dahil) Facepunch giriş sayfasına popup açıp `ReactNativeWebView` nesnesi
enjekte ediyor. Modern Chrome bunu aynı köken politikasıyla engelliyor.
Çözüm: `?returnUrl=` ile normal yönlendirme. `register.py` içinde; eski
yöntem `/legacy` yolunda yedek duruyor.

**2. FCM soketi sessizce ölüyordu.** Bağlı görünüyor, thread yaşıyor,
hiçbir mesaj gelmiyor. Bu bağlantıda heartbeat yok (`interval_ms=0`), yani
pasif sessizlik ölümü göstermiyor. Çözüm: **canlılık probu** — 4 dakikada
bir kendimize Expo push atıp geldiğini doğruluyoruz. Gelmezse dinleyici
baştan kuruluyor. `fcm.py::_check_probe`.

**3. Abone yuvası sızdırıyorduk.** Her yeniden başlatmada entity'ye tekrar
abone oluyorduk ama kapanırken bırakmıyorduk. Altı restart sonra
`too_many_subscribers` ve akış tamamen kesildi. Çözüm: abone olmadan önce
`check_subscription_to_entity`, kapanırken `set_subscription(False)`.
`socket.py`.

**4. Sırrı aynı değerle yazınca siliniyordu.** `apply_updates` karşılaştırmayı
geçerli ayara yapıyordu; override kendi ürettiği değere eşit görünüp
"gereksiz" sayılıp siliniyordu. Üstelik `reapply` bellekteki ayarların
üstüne bindiği için silinme yeniden başlatana kadar fark edilmiyordu.
Çözüm: karşılaştırma **override'sız tabana**, `reapply` de tabandan.
`settings_store.py` + `app.py::_base_settings`.

**5. Bölge/kademe eşleşmesi kopuyordu.** Alarm bildirimi her zaman
`entityId` taşımıyor, taşıdığı ad da cihazın adı değil oyunda girilen
**alarm mesajı** olabiliyor. Eşleşemeyince panelden atanan bölge ve kademe
kayboluyor, ETA sessizce yok oluyordu. Çözüm: üç kademeli eşleştirme —
id, ad, sonra tek kayıtlı cihaza atıf. `app.py::_resolve_zone`.

**6. Sistem bilmediğini iddia ediyordu.** Kullanıcı tek HBHF alarmına elle
"kademe 3" atayınca bildirim "C4/roket kademesinde patlama" yazıyordu — ama
sensör patlama görmemişti, insan görmüştü. Çözüm: `SensorKind`
(explosion/presence/unknown) ve metnin buna göre yazılması. Üç test bunu
koruyor.

> **6 numara bu projenin karakteri.** Bir alarm sistemi bilmediği şeyi
> söylememeli. Yeni özellik eklerken bu kuralı koru: ölçülmemiş bir şeyi
> ölçülmüş gibi sunma, belirsizliği bant/etiketle göster.

---

## Mimari

```
FCM push ──┐                          ┌─→ SQLite (store.py)
           ├─→ Sentinel (app.py) ─────┼─→ Discord / ntfy (notify/)
WebSocket ─┘        │                 └─→ Panel SSE (api.py)
                    ↓
            RaidAggregator (raid.py)
                    ↓
              scoring.py → escalation.py → twilio_caller.py
                    ↓
                  eta.py ← base_model.py
```

| Dosya | Sorumluluk |
|---|---|
| `app.py` | Her şeyi birleştirir, olay akışını yönetir |
| `raid.py` | Tetiklemeleri oturuma toplar, gürültüyü bastırır |
| `scoring.py` | Tehdit puanı — bildirim şiddetini ve telefonu belirler |
| `eta.py` + `base_model.py` | Üs grafı, en ucuz yol, süre tahmini |
| `escalation.py` | Telefon zinciri, bütçe, sessiz saatler |
| `team.py` | Takım listesi — tek bağlantı, çok kişi |
| `settings_store.py` | Panelden ayar (kod < .env < settings.json) |
| `rust/fcm.py` | FCM dinleyici + canlılık probu |
| `rust/socket.py` | WebSocket gözetimi, abonelik yönetimi |
| `web/index.html` | Panel — tek dosya, derleme yok |

**Enjekte edilen çözümleyiciler.** `RaidAggregator` puanlama, ETA ve üs
modelini bilmez; `severity_for`, `detail_for`, `context_for` ile dışarıdan
bağlanır. Bu ayrımı bozma — toplayıcının bunları bilmesi katmanları
birbirine yapıştırır.

---

## Doğrulanmamış varsayımlar

| Varsayım | Yanlışsa ne olur | Nasıl anlaşılır |
|---|---|---|
| Entity aboneliği alarmın bildirim cooldown'ına tabi değil | Ardışık patlamalar sayılamaz, ETA hızı bozulur | Üç C4'ü 20 sn arayla patlat, tetikleme sayısı 3 mü |
| Twilio Türkçe seslendiriyor | Arama sessiz/anlaşılmaz | `sentinel test-call` |
| Termometre devresi üç alarmı ayrı tetikliyor | Patlayıcı tipi ayırt edilemez | Satchel at, S1+S2 yanmalı, S3 yanmamalı |
| 25 sn bölge geçiş süresi | ETA sistematik kayar | Gerçek raidde tahmin/gerçek farkı |

Son ikisi hiç test edilmedi — kullanıcının tek alarmı var, termometre
devresi kurulmadı.

---

## Panel — tamamlananlar

Listedeki yedi eksikten altısı kapatıldı:

- ✅ **Üstlenme** — raid kartında "Üstlendim · 30 dk / 2 saat". Telefon
  zinciri o bölge için susturulur; `escalation.acknowledge()`. Bölge
  bekleme süresinden ayrı ve onun yerine geçmez.
- ✅ **Olay arşivi** — `Arşiv` sekmesi. `/api/history` geçmiş
  `raid_started → raid_ended` çiftlerini oturuma dönüştürüyor; kapanmamış
  olanlar "sürüyor" işaretli.
- ✅ **Analitik** — aynı sekmede. Bölge başına saldırı/ciddi/yanlış alarm,
  cihaz başına tetikleme, aylık harcama. *Yanlış alarm = kritiğe hiç
  ulaşmamış oturum.*
- ✅ **Kimlik doğrulama** — `PANEL_TOKEN` ayarı. Doluysa `/api/*` uçları
  `X-Panel-Token` istiyor; panel anahtarı localStorage'da tutuyor ve 401
  alınca soruyor. Sayfanın kendisi korunmuyor (içinde veri yok).
- ✅ **Olay filtresi** — şiddet seçici + metin araması + `görünen/toplam`
  sayacı. Panel tarafında, sunucuya ek yük yok.
- ✅ **Üs haritası** — SVG topoloji. Yerleşim koordinat gerektirmiyor:
  sütunlar hedefe olan adım sayısından türetiliyor. Saldırı altındaki
  bölge ve TC'ye giden yol canlı vurgulanıyor.

**Kalan tek madde:** Ayarlarda kaydedilmemiş değişiklik uyarısı yok.

Yeni zayıflık adayları (henüz sorun değil, büyüyünce olur):

- Arşiv 30 günle sınırlı ve sayfalama yok
- Analitik "yanlış alarm" tanımı kaba — kritiğe ulaşmayan her oturum
  sayılıyor, oysa bazıları gerçek ama küçük olaylar olabilir
- Olay tablosu büyüdükçe `raid_stats` sorgusu yavaşlar (indeks var ama
  tam tarama yapıyor)

---

## Sırada ne var

**Önce doğrulama** (kod yazmadan):

1. `sentinel test-call` — Türkçe seslendirme çalışıyor mu
2. Sismik sensör + termometre devresi kurulup patlayıcı kademesi test
   edilmeli. Şu an sistem gerçek bir patlama hiç görmedi; kullanıcının
   HBHF'si yalnızca insan algılıyor.

**Sonra kod:**

1. Ayarlarda kaydedilmemiş değişiklik uyarısı
2. Arşivde sayfalama / tarih aralığı seçimi
3. Wipe tespiti — eşleştirme geçersizleşince panelde büyük uyarı

---

## Çalıştırma

```bash
cd C:/Users/Admin/Desktop/Projeler/raid-sentinel
./.venv/Scripts/sentinel.exe run          # panel: http://127.0.0.1:8787/
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check src/ tests/
```

**Windows tuzağı:** `pkill` süreci öldürmüyor, port 8787 tutulu kalıyor ve
yeni kopya sessizce çıkıyor (exit 127). PowerShell ile `Stop-Process -Id`
kullan; `netstat -ano | grep :8787` ile PID'i bul.

**Konsol kod sayfası** cp1254; Türkçe karakterli çıktıyı `PYTHONIOENCODING=utf-8`
ile yazdır, yoksa `UnicodeEncodeError` alırsın. (Uygulamanın kendi logu
korumalı, sadece test betiklerinde sorun.)

Panel arayüzünü Rust+ olmadan görmek için: `python scripts/demo_panel.py`

---

## Belgeler

- `README.md` / `README.tr.md` — ne yapar, kurulum, adlandırma, puanlama, ETA
- `DEPLOY.md` / `DEPLOY.tr.md` — Docker/systemd, headless eşleştirme, yedekleme
- İngilizce sürümler GitHub için birincil; Türkçeler `.tr.md` olarak duruyor
- `TESTING.md` — dört seviyeli test planı, saha senaryoları
- Kullanım kılavuzu (Artifact): kurulum, bağlantı mekanizması, devre
  şeması, telefon maliyetleri

---

## Kullanıcıyla çalışma notu

Halit doğrudan ve hızlı geri bildirim veriyor; eleştirisi genellikle
haklı çıkıyor. "Kamera işe yaramaz" dedi — haklıydı, CCTV craft bile
edilemiyor. "PM sıktım, C4 diyor" dedi — sistemin en ciddi dürüstlük
hatasıydı.

Bir şey çalışmadığında **önce ölç, sonra tahmin et.** Bugünkü altı hatanın
hepsi ölçümle bulundu: Expo'ya test push atmak, makbuz sorgulamak, izole
dinleyici çalıştırmak, logdaki `too_many_subscribers`'ı görmek. Varsayımla
gidilseydi hiçbiri bulunamazdı.
