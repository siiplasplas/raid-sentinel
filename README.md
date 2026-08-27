# Raid Sentinel

Rust üssün raid yerken haber veren, kendi kendine barındırılan erken uyarı sistemi.

**Durum: F1–F3 ve panel tamam.** Eşleştirme, FCM dinleyici, cihaz aboneliği, olay
deposu, Discord/ntfy bildirimleri, tehdit skorlaması, telefon eskalasyon zinciri,
TC'ye ETA hesabı ve canlı panel çalışıyor.

Kamera modülü (F4) bilerek yapılmadı: CCTV Camera oyunda **craft edilemiyor**,
sadece Locked Crate/Elite loot'undan çıkıyor. Herkesin bulunduramayacağı bir
donanımın üstüne özellik kurmak doğru değil.

---

## Ne yapıyor

Oyun içine koyduğun Smart Alarm'lar tetiklendiğinde Rust+ üzerinden anında haber
alıyorsun. Ham tetiklemeler doğrudan bildirilmiyor — bir raid sırasında saniyeler
içinde onlarca tetikleme gelir. Bunun yerine olaylar bölgeye göre oturumlarda
toplanıp üç anlamlı bildirime indirgeniyor: saldırı başladı, saldırı sürüyor,
saldırı durdu.

## Kurulum

```bash
python -m venv .venv
.venv/Scripts/pip install -e .
```

Dosya düzenlemene gerek yok — **her şey panelden ayarlanıyor.** Eşleştirmeyi yapıp
sistemi başlat, sonra tarayıcıdan yapılandır:

```bash
.venv/Scripts/sentinel pair
.venv/Scripts/sentinel run
```

Panel `http://127.0.0.1:8787/` adresinde. Ayarlar sekmesinden Discord webhook'unu,
ntfy konusunu, telefon zincirini ve limitleri gir; kaydettiğinde **süreç yeniden
başlamadan devreye girer**.

İstersen `.env` de kullanabilirsin (`cp .env.example .env`) — ilk kurulumu
dosyayla yapmak isteyenler için duruyor. Katman sırası:

```
kod varsayılanları  <  .env  <  panelden yapılan değişiklikler
```

Panelden yapılanlar `data/settings.json` içine yazılır. Bir ayarı varsayılana
döndürürsen override silinir, yani `.env`'i sonradan değiştirmek yine işe yarar.

## Eşleştirme

Bir kereye mahsus:

```bash
sentinel pair
```

Tarayıcı açılır, Steam ile giriş yaparsın. Bu adım Facepunch'ın giriş sayfasına
bağlı ve açılır pencere gerektiriyor — engelleyici açıksa kapat.

Sonra sistemi başlat ve açık bırak:

```bash
sentinel run
```

Oyunda sunucuya gir, `ESC → Rust+ → Pair with Server` yap. Ardından her Smart
Alarm'a bakıp `Pair` de. Eşleştirdiğin her cihaz otomatik kaydedilir ve izlemeye
alınır.

## Cihaz adlandırması

Sistemin tek yapılandırma arayüzü, cihaza oyun içinde verdiğin ad. Ayrı bir panel
doldurmuyorsun:

| Oyun içi ad | Bölge | Sismik kademe |
|---|---|---|
| `Garaj S3` | Garaj | 3 — C4/roket |
| `Airlock S2` | Airlock | 2 — satchel/patlayıcı mermi |
| `Cati S1` | Cati | 1 — el bombası/beancan |
| `Cati HBHF` | Cati HBHF | yok |

Ayırıcı olarak boşluk, `_` veya `-` kullanabilirsin.

### Sismik kademe neden önemli

Sismik Sensör, algıladığı patlamanın türüne göre farklı **güç** üretiyor
(1 = el bombası/beancan, 2 = patlayıcı mermi/satchel, 3 = C4/roket). Rust+ bu
sayıyı vermiyor, sadece açık/kapalı görüyor.

Kademeyi oyun içi devreyle geri kazanıyorsun: sensör çıkışını **1'e ayarlı seri
Electrical Branch**'lerden geçirip üç ayrı Smart Alarm'a bağla. Hangi alarmların
yandığı güç seviyesini termometre gibi kodlar:

```
Sismik Sensör ──> Branch(1) ──┬─> Alarm "Garaj S1"   (her patlamada)
                   │           │
                   └ P-1 ──> Branch(1) ──┬─> Alarm "Garaj S2"   (P>=2)
                               │          │
                               └ P-2 ─────┴─> Alarm "Garaj S3"  (P=3)
```

Böylece hangi patlayıcıyla vurulduğunu biliyorsun — harcadıkları sülfür tahmini
ve ileride ETA hesabı buna dayanacak.

## Tehdit skorlaması ve telefon

Her tetikleme puanlanır ve puana göre kanal seçilir. Amaç iki yönlü: gece 4'te
boşuna uyanmamak, ve telefon faturasını kontrol altında tutmak.

| Kanıt | Puan |
|---|---|
| C4/roket kademesinde patlama | +60 |
| Satchel/patlayıcı mermi kademesi | +40 |
| Hafif patlayıcı (el bombası/beancan) | +15 |
| İki veya daha fazla ayrı sensör | +25 |
| 3+ tetikleme | +15 |
| 90 saniyeden uzun süredir devam | +15 |
| Takım arkadaşı bölgeye yakın | −30 |

Eşikler: **YÜKSEK** 60+, **ORTA** 35+, **DÜŞÜK** 15+.

Pratikte: tek bir C4 patlaması anında YÜKSEK olur — kimse kendi üssünü C4'lemez,
ikinci kanıta gerek yok. Tek bir HBHF tetiklemesi ise hiçbir şey yapmaz; yanından
geçen biri telefonu çaldırmamalı.

Takım arkadaşı cezası **yalnızca patlayıcı kanıtı yokken** uygulanır. Bu ceza HBHF
sensörlerinin ürettiği sahte alarmı elemek için var; sismik sensör patlama
görmüşse o kanıt tartışılmaz.

### Eskalasyon zinciri

YÜKSEK tehditte kişiler sırayla aranır, **ilk cevap veren zinciri durdurur**.
Telesekretere düşen arama cevap sayılmaz (`MachineDetection`), yoksa telefonun
sesli mesaja düşmesi zinciri yanlışlıkla kapatırdı.

Üç emniyet supabı var ve üçü de bilerek "arama yapmama" yönünde hata yapar:

- **Aylık bütçe tavanı** — Türkiye mobiline dakikası ~0,29 USD. Tavan dolduğunda
  arama durur, Discord ve ntfy çalışmaya devam eder.
- **Bölge bekleme süresi** — aynı raid için tekrar tekrar aranmazsın.
- **Sessiz saatler** — gece yalnızca YÜKSEK tehdit telefon çaldırır.

Arama, TwiML'i istek içinde gönderiyor. Bu sayede **makinenin internetten
erişilebilir olması gerekmiyor** — NAT arkasındaki bir VPS'te sorunsuz çalışır.
Sonuç webhook yerine arama kaydı yoklanarak öğreniliyor.

> **Türkçe seslendirme doğrulanmadı.** Twilio'nun desteklenen ses tablosunda
> `tr-TR` görünmüyor. AWS Polly'de Filiz ve Burcu var ama Twilio'nun bunları
> yayınlayıp yayınlamadığı belirsiz. `sentinel test-call` ile bunu bir raid
> beklemeden dene; ses gelmezse `.env` içinde `TWILIO_LANGUAGE=en-US` ve
> `TWILIO_VOICE=Polly.Joanna` yap.

## Üs modeli ve ETA

`data/base.json` dosyasına üssün kaba haritasını yazarsan sistem "TC'ye ne kadar
kaldı" hesaplayabilir. En kolayı panelin **Üs** sekmesi — örnek şablon hazır
gelir, düzenleyip kaydedersin. Terminalden doğrulamak için:

```bash
.venv/Scripts/sentinel base
```

Bölge adları, oyun içi cihaz adlarındaki bölge adlarıyla **aynı olmalı** —
`Garaj S3` adlı alarm, üs tanımındaki `Garaj` bölgesine bağlanır.

Her bağlantı iki bölgeyi ayıran engelleri listeler. Raider'ın izleyeceği yol,
saldırdığı bölgeden TC'ye giden **en ucuz** yol (Dijkstra).

```
ETA = kalan_patlayıcı × tetikleme_aralığı + bölge_geçiş_süresi
```

Hız sabit bir katsayı değil, o anki raidden **ölçülüyor**. İlk ölçümlerde güven
"düşük", ölçüm biriktikçe daralıyor:

```
 patlama  bolge       kalan      ETA           bant  guven
       2  Kompound       13     5.6d    2.5-10.3 d  dusuk
       3  Kompound       13     5.6d    4.2-6.9  d  orta
       6  Kompound       13     5.6d    4.2-6.9  d  iyi
       -- garaji gectiler, Airlock sensoru tetikleniyor --
       4  Airlock         3     1.4d    0.8-2.1  d  orta
```

Belirsizlik gizlenmiyor, bant olarak veriliyor: 3. kademe hem C4 hem roket
olabilir ve ikisinin duvar başına adedi farklı — o fark bandın içinde.

### ETA ne zaman hesaplanmaz

Uydurma bir sayı vermektense hiç vermemeyi tercih ediyor. Şu durumlarda sessiz
kalır: üs tanımı yok, bölge tanımda yok, sismik kademe bilinmiyor (patlayıcı tipi
olmadan hız patlayıcıya çevrilemez), veya hız ölçecek kadar veri yok.

### Bilinen sınır

Aynı bölgede kalan patlayıcı sayısı, o bölgeden çıkan engelin maliyetiyle
sınırlanıyor. Yani ETA bölge içinde sabit kalır ve ancak bir sonraki bölgenin
sensörü tetiklendiğinde düşer — basamaklı bir geri sayım. Sensörlerin verdiği
bilgiyle daha iyisi yapılamıyor; olduğundan iyi göstermektense böyle bırakıldı.

## Komutlar

```bash
sentinel base           # Üs tanımını doğrula ve yolları göster
sentinel pair           # Rust+ eşleştirmesi (bir kez)
sentinel run            # Sistemi çalıştır
sentinel doctor         # Yapılandırmayı kontrol et
sentinel test-notify    # Bildirim kanallarını dene
sentinel test-call      # Gerçek bir telefon araması dene
```

## Panel

`sentinel run` çalışırken `http://127.0.0.1:8787/` adresinde canlı bir olay
müdahale konsolu var:

- **Aktif saldırılar** — bölge, tehdit seviyesi, TC'ye işleyen geri sayım, güven
  bandı, kalan yol merdiveni ve "neden alarm verdim" gerekçeleri
- **Olaylar** — canlı akan olay listesi
- **Cihazlar** — eşleştirilmiş cihazlar. Her birine bölge ve sismik kademe
  atayabilir, kaydı silebilir, **test tetiklemesi** gönderebilirsin. Test
  tetiklemesi ayrı bir yol değil: toplayıcı, puanlama ve bildirim kanalları
  dahil gerçek boru hattından geçer, yani raid beklemeden tüm zinciri
  doğrulayabilirsin.
- **Üs** — görsel editör. Bağlantı ekle, iki ucuna bölge yaz, engelleri açılır
  listeden seç (her seçeneğin C4 maliyeti yanında yazılı), hedef bölgeyi belirle.
  Maliyet anlık hesaplanır, kaydederken doğrulanır ve devreye girer. Elle
  düzenlemek isteyenler için JSON görünümü de var.
- **Ayarlar** — bildirim kanalları, telefon zinciri, eşikler ve limitler; ayrıca
  "Bildirimleri dene" ve "Telefonu dene" düğmeleri
- **Kurulum** — canlı kontrol listesi ve oyun içi rehber: malzeme listesi, devre
  şeması, adlandırma kuralı, eşleştirme adımları
- **Sistem** — FCM sessizliği, bağlantı durumu, yeniden bağlanma sayısı, aylık
  harcama

### Cihazlar nasıl eklenir

Panelden eklenmez — **oyun içinden eşleştirilir**. Rust+ yalnızca oyunda `Pair`
dediğin cihazları programa tanıtır. Program açıkken oyunda eşleştirdiğin cihaz
saniyeler içinde listede belirir. Bu akış panelin Cihazlar ve Kurulum
sekmelerinde adım adım anlatılıyor.

### Sessiz yanlış yapılandırma uyarısı

Cihaz adındaki bölge ile üs tanımındaki düğüm adı birebir tutmazsa alarm çalışır
ama **ETA sessizce kaybolur**. Kurulum sekmesi bunu yakalar ve uyuşmayan bölgeleri
listeler; ilgili cihaz kartında da "üs tanımında yok" rozeti çıkar.

Sırlar (webhook adresi, token'lar) panele **asla açık gönderilmiyor** — yalnızca
"tanımlı ···1234" biçiminde. Boş bıraktığın sır alanı değişmez; silmek için `-`
yaz.

Panel tek bir HTML dosyası; derleme adımı, `node_modules` ve dış kaynak yok
(bir test bunu doğruluyor — internetsiz bir VPS'te de açılır). Veri tek bir SSE
bağlantısından geliyor: olaylar oluştukları anda, durum birkaç saniyede bir. Geri
sayım tarayıcıda işliyor, sunucudan saniye saniye veri beklenmiyor.

Kritik olayda sesli uyarı için sağ üstteki **ses** düğmesini aç.

Arayüzü Rust+ eşleştirmesi olmadan görmek için:

```bash
.venv/Scripts/python scripts/demo_panel.py
```

### Uçlar

`/health` dış izleme servisleri (dead-man switch) için: sistem sağlıksızsa **503**
döner, böylece sessizce öldüğünde haberin olur. Ayrıca `/api/state`, `/api/events`,
`/api/entities`, `/api/raids`, `/api/base` ve `/api/stream` (SSE).

Varsayılan olarak yalnızca yerel arayüze bağlanır — dışarı açacaksan önüne ters
vekil ve kimlik doğrulama koy, burada oturum yönetimi yok.

## Sunucuya kurulum

Docker veya systemd ile: **[DEPLOY.md](DEPLOY.md)**. Headless sunucuda
eşleştirmenin nasıl yapılacağı da orada.

Sisteme güvenmeden önce yapılacak testler: **[TESTING.md](TESTING.md)**.

## Nerede çalıştırmalı

**Ev bilgisayarında değil.** PC kapalıyken — yani tam offline raid anında —
sistem de ölü olur. Küçük bir VPS yeter.

## Sessiz arızaya karşı

Bu tür sistemlerin en büyük katili, her şeyin çalışıyor görünüp hiçbir şeyin
gelmemesi. Kullandığımız kütüphanenin iki bilinen açığı var ve ikisi de burada
kapatıldı:

- **Yeniden bağlanma yok.** `rustplus` bağlantı koptuğunda döngüden çıkıyor ve
  bir daha denemiyor. Geri çekilmeli yeniden bağlanma, kopma sonrası abonelikleri
  yenileme ve düzenli gerçek istekle sağlık kontrolü eklendi.
- **FCM sessizce ölüyor** (olijeffers0n/rustplus#75). `PushReceiver` doğrudan
  kullanılıyor; `time_last_message_received` ile gerçek canlılık ölçülüyor.
  90 dakika sessizlikte soket kapatılıp yeniden bağlanma zorlanıyor, 150 dakikada
  dinleyici baştan kuruluyor.

Ayrıca `/health` ucu, bağlantı sağlıksızsa 503 döner — dışarıdan bir izleme
servisiyle (dead-man switch) bağlayabilirsin.

## Testler

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check src/
```

## Veri güncelliği

`src/sentinel/raiddata.py` içindeki raid maliyet tabloları oyun sürümüne bağlı ve
Facepunch bunları periyodik olarak yeniden dengeliyor. Doğrulama tarihi dosyanın
başında yazılı. **rustlabs.com artık yok** (skin pazarına dönüştü) — oradan gelen
hiçbir rakam kullanılmadı.

Bilinen boşluk: Mayıs 2026'da gelen **Mortar** hiçbir tabloda yok, ve sismik
sensörün onu hangi kademede algıladığı bilinmiyor.
