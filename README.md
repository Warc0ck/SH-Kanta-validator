# SH-Kanta-validator

Streamlit-käyttöliittymä Kannan SHARK-validointipalvelulle. Sovellus lukee
siirtokehyksen ja CDA-asiakirjan, tarkistaa syötteet paikallisesti ja lähettää
ne käyttäjän käynnistämällä pyynnöllä validointipalveluun.

## Asennus ja käynnistys

Käytä Python 3.10:tä tai uudempaa. Luo virtuaaliympäristö projektin hakemistossa
ja asenna määritellyt riippuvuudet ennen käynnistystä:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python SHKanta-www-validator.py
```

Suora Python-käynnistys avaa Streamlit-sovelluksen samassa Python-ympäristössä.
macOS- ja Linux-ympäristöissä voit käynnistää tiedoston myös komennolla
`./SHKanta-www-validator.py`. Vaihtoehtoinen käynnistystapa on edelleen
`streamlit run SHKanta-www-validator.py`. Mahdolliset Streamlitin
komentoriviargumentit välitetään myös suorasta käynnistyksestä, esimerkiksi
`python SHKanta-www-validator.py --server.port 8502`.

Sovellus käyttää koodissa määriteltyä, julkaistun käyttöohjeen mukaista
HTTP-palveluosoitetta. Käynnistys ei asenna paketteja automaattisesti.

## Syötteet ja henkilötunnusten tarkistus

Syötä siirtokehys ja CDA-asiakirja XML-tiedostoina tai tekstinä. Asiakirjan on
sisällettävä HL7:n `urn:hl7-org:v3`-nimialueen `ClinicalDocument`-elementti.
Siirtokehyksen palvelupyyntökoodin on oltava yksiselitteinen. Kehyksen
`id/@root`- ja `setId/@root`-tunnisteiden on täsmättävä asiakirjan tunnisteisiin
ennen lähetystä. Palvelupyyntö erotetaan muista `reasonCode`-elementeistä
sen koodistotunnisteen perusteella.

Käytä vain testiaineistoa. Sovellus estää lähetyksen, jos se löytää kelvollisen
suomalaisen henkilötunnuksen, jonka yksilönumero ei ala numerolla 9.
Yksilönumero tarkoittaa kolmea numeroa vuosisatamerkin ja tarkistusmerkin välissä.
Tunnuksen päivämäärän ja tarkistusmerkin on myös oltava kelvollisia.

Paikallinen tarkistus käsittelee XML:n tekstit ja attribuutit sekä tuetut
tekstimuotoiset upotetut sisällöt, kuten XML- ja JSON-tekstin ja Base64:ksi
koodatun tekstin. Tarkistus ei pura binäärisiä liitteitä. Jos syötteessä on
nimenomaisesti ilmoitettu binäärinen sisältö, jota tarkistus ei tue, lähetys
pysäytetään. Henkilötunnusten tarkistus ei korvaa aineiston anonymisointia:
myös muut henkilötiedot on poistettava testiaineistosta.

## Henkilötietokenttien korvaus

Syötettyjen sanomien tunnettuja henkilötietokenttiä voi korvata paikallisesti
painikkeella **Korvaa henkilötietokentät**. Korvaus muodostaa uudet XML-kopiot:
alkuperäiset syötteet ja tiedostot säilyvät muuttumattomina. Korvatut sanomat
näkyvät käyttöliittymässä ja ovat ladattavissa UTF-8-muotoisina XML-tiedostoina.

Korvatun asiakirjan `urn:hl7finland`-nimiavaruuden JSON-sisällöt puretaan
Base64-muodosta ja näytetään sisennettyinä asiakirjan XML:ssä. Myös ladattava
asiakirjakopio sisältää JSONin luettavana tekstinä, joten sen kentät voi
tarkistaa XML:n sisältä. JSONin teksti on CDATA-osiossa, jotta esimerkiksi
merkit `<` ja `&` säilyvät oikein.

Korvaustoiminnot ovat `validator_replacer.py`-moduulissa. Ne käsittelevät
XML:n henkilötunnus-, nimi- ja syntymäaikakenttiä sekä upotetun JSON:n
tunnettuja henkilötieto- ja yhteystietokenttiä. XHTML-näyttömuoto korvataan
tyhjällä näyttömuodolla. XML:n nimiavaruudet sekä asiakirjan `id/@root`- ja
`setId/@root`-tunnisteet säilyvät.

Tarkista korvatut sanomat ja lataa ne tarvittaessa ennen lähettämistä.
Vahvista tarkistus valintaruudulla ja valitse **Validoi korvatut sanomat**.
Korvauspainike ei lähetä tietoja verkkoon. Validointi tekee edelleen normaalit
paikalliset tarkistukset ennen palvelupyyntöä. Syötteiden muuttaminen poistaa
aiemmat korvauskopiot ja niiden lähetysvahvistuksen.

Ennen lähetystä sovellus koodaa asiakirjan JSON-sisällöt takaisin UTF-8:n
Base64-muotoon. Tarkistettava ja ladattava kopio säilyy luettavana. Ladatun
tarkistus-XML:n voi myös syöttää myöhemmin asiakirjakenttään ja validoida
painikkeella **Suorita validointi**: JSON koodataan silloinkin ennen lähetystä.

Korvaus käsittelee tunnettuja kenttiä, eikä se takaa koko aineiston
anonymisointia. Myös vapaateksti, muut kentät ja liitteet on tarkistettava.
`replacer.py` säilyttää vanhat tuontinimet, mutta käyttöliittymätoiminnot
ovat Streamlit-sovelluksessa.

## Versiopäivitykset

Version 3.3 muutoskooste ja aiempien versioiden historia avautuvat erilliseen
ikkunaan painikkeella **Versiopäivitykset**. Uusin versio näkyy avattuna,
ja vanhempien versioiden muutokset voi avata samasta ikkunasta. Historian
avaaminen säilyttää syötteet, korvauskopiot, tarkistusvahvistuksen ja
validointituloksen eikä käynnistä uutta validointipyyntöä.

## Vastaukset

Onnistunut validointi on tyhjä HTTP 200 -vastaus. Virhevastaukset säilyvät
käyttöliittymässä ja ovat ladattavissa myös silloin, kun JSON-vastauksesta
puuttuu tekstimuotoinen `description`-kenttä. HTML-vastaukset näytetään tekstinä.
HTTP 200 -vastauksen odottamaton sisältö näytetään tarkistettavana vastauksena,
eikä sitä tulkita onnistuneeksi validoinniksi.

Lähetettäviin XML-kenttiin lisätään tarvittaessa yksi XML-alkumäärittely ja
CRLF/CR-rivinvaihdot muunnetaan LF-muotoon. Tämä varmistaa yhteensopivuuden
validointipalvelun kanssa; alkuperäisiä tiedostoja ei muokata.
`Request Rejected` -HTML-vastauksen tukitunniste näytetään virheilmoituksessa.

## Testit

Regressiotestit käyttävät Pythonin standardikirjaston `unittest`-moduulia.
Ne eivät lähetä sanomia validointipalveluun:

```sh
python -m unittest discover -s tests
```
