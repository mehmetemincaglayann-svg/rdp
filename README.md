# PyRemote

Python ile yazılmış, TeamViewer benzeri uzak masaüstü kontrol programı.
Bağlantı iki bilgisayar arasında **doğrudan (P2P)** kurulur; kendi sunucunuzu
kurmanız gerekmez.

- Ekran görüntüleme, fare ve klavye kontrolü
- ID + parola ile internet üzerinden doğrudan bağlantı (WebRTC veri kanalı)
- Dosya gönderme / alma, pano (kopyala-yapıştır) paylaşımı
- Çoklu monitör, görüntü kalitesi seçimi
- Gözetimsiz erişim (kalıcı parola)
- Uçtan uca şifreleme (SPAKE2 + ChaCha20-Poly1305)

## Kullanım

| Program | Nerede çalışır |
|---|---|
| `PyRemoteHost.exe` | Kontrol edilecek Windows bilgisayar |
| `PyRemoteViewer.exe` | Kontrol eden bilgisayar |

1. Kontrol edilecek bilgisayarda `PyRemoteHost.exe` dosyasını açın. Pencerede
   "Bağlantıya hazır" yazınca ID ve parola kullanılabilir.
2. Diğer bilgisayarda `PyRemoteViewer.exe` dosyasını açıp ID ve parolayı girin.
   Bağlantının kurulması 10-20 saniye sürebilir.

Kaynak koddan çalıştırmak için (Python 3.10+):

```bash
pip install -r requirements.txt
```

```bash
python -m pyremote.host
```

```bash
python -m pyremote.viewer
```

## İki bağlanma yolu

İzleyicide "ID ya da IP adresi" alanına ikisinden biri yazılır:

| | ID ile (ör. `431 042 125`) | IP adresi ile (ör. `192.168.1.20`) |
|---|---|---|
| Aracı servis | Tanışma için ücretsiz açık servisler | Hiçbiri |
| Aynı ağda | Çalışır | Çalışır, en hızlı ve en basit yol |
| Farklı ağlarda | İki ağ da uygunsa çalışır | Ana makinenin modeminde port yönlendirme gerekir |
| Adres değişirse | ID sabit kalır | Yeni IP adresini bilmeniz gerekir |

### IP adresi ile doğrudan bağlantı

Ana makine penceresindeki **Doğrudan adres** satırı (ör. `192.168.1.20:47600`) aynı
ağdaki bilgisayarlardan doğrudan kullanılabilir. İlk açılışta Windows Güvenlik Duvarı
izin isterse onaylayın.

Farklı bir ağdan bağlanmak için ana makinenin modeminde 47600 numaralı TCP bağlantı
noktasını ana makinenin yerel IP adresine yönlendirin ve izleyicide modemin genel IP
adresini yazın. Bu yol ancak internet sağlayıcınız size gerçek bir genel IP veriyorsa
çalışır (CGNAT arkasında çalışmaz). Bağlantı noktası ayar dosyasındaki `direct_port`
ile değiştirilir; `0` yazılırsa dinleme kapatılır.

Her iki yolda da parola doğrulaması ve uçtan uca şifreleme aynıdır.

## Bağlantı nasıl kuruluyor?

Farklı ağlardaki iki bilgisayar modemlerin (NAT) arkasında olduğundan birbirinin
adresini kendi başına bulamaz. PyRemote bunun için herkese açık, ücretsiz iki tür
servis kullanır; hiçbirini sizin kurmanız gerekmez:

- **STUN** (Google, Cloudflare): her bilgisayar kendi genel adresini öğrenir.
- **Açık MQTT sunucuları** (HiveMQ, EMQX, Mosquitto): iki taraf birkaç KB'lık
  bağlantı bilgisini değiş tokuş eder ("tanışma"). Üçü aynı anda kullanılır; biri
  çalışmasa da diğerleri taşır.

Tanışmadan sonra ekran görüntüsü, girdi ve dosyalar bu servislere uğramadan iki
bilgisayar arasında doğrudan akar.

### Doğrudan bağlantı her ağda kurulamaz

İki taraftan birinin ağı "simetrik NAT" kullanıyorsa (mobil internet, CGNAT kullanan
bazı sağlayıcılar, sıkı kurumsal ağlar) doğrudan bağlantı kurulamaz ve izleyici bunu
bildirir. Bu durumda seçenekler: taraflardan birinin başka bir ağa geçmesi ya da
ayar dosyasına bir TURN (aktarma) sunucusu eklenmesi.

Servis listeleri ayar dosyasından değiştirilebilir
(`%APPDATA%\PyRemote\config.json`, iki tarafta da aynı olmalı):

```json
{
  "brokers": ["wss://broker.hivemq.com:8884/mqtt"],
  "ice_servers": [
    {"urls": ["stun:stun.l.google.com:19302"]},
    {"urls": "turn:turn.ornek.com:3478", "username": "kullanici", "credential": "parola"}
  ]
}
```

## Gözetimsiz erişim

Ana makine penceresinde **Kalıcı parola ayarla…** düğmesini kullanın ya da:

```bash
PyRemoteHost.exe --set-password
```

Ana makinenin oturum açılınca kendiliğinden başlaması için `PyRemoteHost.exe`
dosyasının kısayolunu Başlangıç klasörüne koyun (Win+R → `shell:startup`).

## .exe üretme

PyInstaller yalnızca çalıştığı işletim sistemi için derler; derleme Windows'ta
yapılmalıdır. Windows'ta `build.bat` dosyasına çift tıklayın; dosyalar `dist`
klasöründe oluşur. GitHub'da ise Actions sekmesindeki "Windows exe" iş akışı aynı
dosyaları üretir.

İmzasız `.exe` dosyaları için Windows SmartScreen ve bazı antivirüsler uyarı verebilir.

## Güvenlik modeli

- Parola ağ üzerinden hiç gönderilmez. İki taraf SPAKE2 ile parolayı bildiklerini
  kanıtlar ve bir oturum anahtarı üretir. Tanışma sunucuları ne parolayı ne de
  içeriği görebilir; araya giren biri parolayı çevrimdışı deneme-yanılma ile de kıramaz.
- Tanışma iletileri ID'den türetilen bir anahtarla şifrelenir. ID'nizi bilen biri bu
  iletilerdeki IP adreslerinizi görebilir (ID gizli bir bilgi değildir); oturuma
  ise parola olmadan giremez.
- Art arda 5 hatalı denemeden sonra ana makine girişleri 5 dakika (her yeni
  hatada katlanarak, en çok 1 saat) kilitler.
- Kalıcı parola diskte düz metin olarak saklanmaz (scrypt ile türetilmiş anahtar saklanır).
- Ana makine penceresi, bağlantı varken kırmızı bir uyarı ve bağlanan IP adresini gösterir.
- Gönderilen dosyalar ana makinede yalnızca `İndirilenler` klasörüne yazılır ve var
  olan dosyanın üzerine yazılmaz. Bağlanan kişi ise ana makinedeki kullanıcının
  okuyabildiği her dosyayı indirebilir; parolayı buna göre koruyun.

## Bilinen sınırlar

- Fare/klavye kontrolü yalnızca Windows ana makinede çalışır (diğer sistemlerde yalnızca izleme).
- UAC onay pencereleri, kilit ekranı ve yönetici olarak çalışan programlar, ana makine
  normal kullanıcı olarak çalışırken kontrol edilemez (Windows kısıtı). Ctrl+Alt+Del gönderilemez.
- Ücretsiz açık servislerin çalışma garantisi yoktur; hepsi birden erişilemez olursa
  yeni bağlantı kurulamaz (açık oturumlar etkilenmez).
- Görüntü JPEG olarak ve yalnızca değişen bölge gönderilerek aktarılır; video ve oyun için uygun değildir.
- Ses aktarımı yoktur. Pano yalnızca metin taşır.

## Test

```bash
python tests/test_e2e.py
```

İnternet ve gerçek servislerle deneme:

```bash
python tests/test_public.py
```

## Dizin yapısı

```
pyremote/
  p2p.py            WebRTC ile doğrudan bağlantı kurma
  signaling.py      açık MQTT sunucuları üzerinden tanışma
  transport.py      veri kanalı uyarlayıcısı (parçalama, akış denetimi)
  crypto.py         SPAKE2 el sıkışması, şifreli kanal, deneme kilidi
  protocol.py       ileti türleri
  config.py         ayar dosyası (ID, servis listeleri, kalıcı parola anahtarı)
  host/service.py   oturum yönetimi: ekran, girdi, pano, dosya
  host/backend.py   ekran yakalama (mss) ve Windows girdisi
  host/win_input.py SendInput ile fare/klavye
  host/keymap.py    tuş çevirisi
  host/gui.py       ana makine penceresi
  viewer/client.py  izleyicinin ağ tarafı
  viewer/gui.py     izleyici penceresi
```
