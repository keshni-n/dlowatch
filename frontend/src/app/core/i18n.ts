import { Injectable, signal } from '@angular/core';

export type Lang = 'kreol' | 'fr' | 'en';

/** Language for first-time visitors. Their own choice is remembered after that. */
export const DEFAULT_LANG: Lang = 'en';

// Public pages only. Please have a native speaker check the Kreol and French.
const T: Record<string, Record<Lang, string>> = {
  heroTitle: { kreol: 'Ed nou trouv bann fwit dlo.', fr: "Aidez-nous a trouver les fuites d'eau.", en: 'Help us find water leaks.' },
  heroSub: {
    kreol: 'Si ou trouv dlo pe koule kot li pa ti bizin, dir nou dan 30 segonn.',
    fr: "Si vous voyez de l'eau couler la ou il ne faut pas, dites-le nous en 30 secondes.",
    en: "If you see water running where it shouldn't, tell us in 30 seconds.",
  },
  reportCta: { kreol: 'Rapor enn fwit dlo', fr: 'Signaler une fuite', en: 'Report a leak' },
  track: { kreol: 'Swiv mo rapor', fr: 'Suivre mon signalement', en: 'Track my report' },
  trackSub: { kreol: 'Met kod ki ou ti gagne', fr: 'Entrez le code recu', en: 'Enter the code you got' },
  myCode: { kreol: 'Mo kod', fr: 'Mon code', en: 'My code' },
  get: { kreol: 'Rod', fr: 'Voir', en: 'Get' },
  noLogin: { kreol: 'Pa bizin login.', fr: 'Pas besoin de compte.', en: 'No account needed.' },
  noName: { kreol: 'Nou pa demann ou nom ni ou nimero.', fr: 'On ne demande ni nom ni numero.', en: 'We never ask for your name or number.' },
  goesTo: { kreol: 'Ou rapor al direk kot sant kontrol dlo.', fr: "Votre signalement va directement au centre de controle.", en: "Your report goes straight to the water utility's control room." },
  staff: { kreol: 'Staff?', fr: 'Personnel ?', en: 'Staff?' },
  title: { kreol: 'Ou finn trouv enn fwit dlo?', fr: 'Vous avez vu une fuite ?', en: 'Spotted a water leak?' },
  takes: { kreol: 'Pran 30 segonn.', fr: 'Cela prend 30 secondes.', en: 'Takes 30 seconds.' },
  photo: { kreol: 'Pran enn foto', fr: 'Prendre une photo', en: 'Take a photo' },
  photoOpt: { kreol: 'Si ou kapav', fr: 'Si possible', en: 'If you can' },
  photoAdded: { kreol: 'Foto ajoute', fr: 'Photo ajoutee', en: 'Photo added' },
  remove: { kreol: 'Tir li', fr: 'Retirer', en: 'Remove' },
  photoChecking: { kreol: 'Pe gete foto-la...', fr: 'Verification de la photo...', en: 'Checking the photo...' },
  photoOk: { kreol: 'Foto-la kler', fr: 'La photo est nette', en: 'Photo looks clear' },
  photoDark: { kreol: 'Foto-la tro nwar. Esey ankor ar flash-la.', fr: 'La photo est trop sombre. Reessayez avec le flash.', en: 'This photo is too dark. Try again with the flash on.' },
  photoBlank: { kreol: 'Pa trouv nanye lor foto-la. Get si kamera-la pa kouver.', fr: "On ne voit rien sur la photo. Verifiez que l'objectif n'est pas couvert.", en: "We can't see anything in this photo. Check the camera is not covered." },
  photoBlurry: { kreol: 'Foto-la flou. Tini telefonn-la bien fer ek esey ankor.', fr: 'La photo est floue. Tenez le telephone immobile et reessayez.', en: 'This photo is blurry. Hold the phone still and try again.' },
  photoNotRelevant: { kreol: 'Foto-la pa paret montre dlo ouswa enn tiyo.', fr: "La photo ne semble pas montrer de l'eau ou un tuyau.", en: "This photo doesn't seem to show water or a pipe." },
  retake: { kreol: 'Repran foto', fr: 'Reprendre', en: 'Retake' },
  keepPhoto: { kreol: 'Gard li kanmem', fr: 'Garder quand meme', en: 'Keep it anyway' },
  photoKept: { kreol: 'Ou finn gard sa foto-la.', fr: 'Vous avez garde cette photo.', en: 'You kept this photo.' },
  where: { kreol: 'Kot ou ete la?', fr: 'Ou etes-vous ?', en: 'Where is it?' },
  useLoc: { kreol: 'Servi mo lokalizasion', fr: 'Utiliser ma position', en: 'Use my location' },
  tapMap: { kreol: 'Ou kapav tap lor lakart pou sanz plas.', fr: 'Touchez la carte pour changer.', en: 'Tap the map to move the pin.' },
  locFail: { kreol: 'Pa finn kapav gagn ou lokalizasion. Tap lor lakart.', fr: 'Position introuvable. Touchez la carte.', en: 'Could not get your location. Tap the map instead.' },
  what: { kreol: 'Ki ou trouve?', fr: 'Que voyez-vous ?', en: 'What do you see?' },
  note: { kreol: 'Enn ti detay (pa obligatwar)', fr: 'Un detail (facultatif)', en: 'A short note (optional)' },
  send: { kreol: 'Avoy rapor', fr: 'Envoyer', en: 'Send report' },
  sending: { kreol: 'Pe avoye...', fr: 'Envoi...', en: 'Sending...' },
  needPlace: { kreol: 'Swazir kot li ete avan.', fr: "Choisissez d'abord l'endroit.", en: 'Please choose where it is first.' },
  needKind: { kreol: 'Swazir ki ou trouve.', fr: 'Choisissez ce que vous voyez.', en: 'Please choose what you see.' },
  thanks: { kreol: 'Mersi!', fr: 'Merci !', en: 'Thank you!' },
  sent: { kreol: 'Ou rapor finn ale.', fr: 'Votre signalement est envoye.', en: 'Your report was sent.' },
  noteCleaned: {
    kreol: 'Nou finn tir nimero telefonn ek email dan ou mesaz, pou proteze ou.',
    fr: 'Nous avons retire les numeros de telephone et emails de votre message, pour vous proteger.',
    en: 'We removed phone numbers and emails from your note, to protect you.',
  },
  yourCode: { kreol: 'Ou kod', fr: 'Votre code', en: 'Your code' },
  keepCode: { kreol: 'Gard sa kod-la pou swiv ou rapor.', fr: 'Gardez ce code pour le suivi.', en: 'Keep this code to follow your report.' },
  others: { kreol: 'lezot dimounn inn rapor pre ar isi', fr: 'autres personnes ont signale pres d’ici', en: 'other people reported near here' },
  othersSub: { kreol: 'Sa plas-la deza lor lalert.', fr: 'Cette zone est deja en alerte.', en: 'This area is already on alert.' },
  status: { kreol: 'Kot li ete', fr: 'Ou en est-on', en: 'Status' },
  refresh: { kreol: 'Rafresi', fr: 'Actualiser', en: 'Refresh' },
  home: { kreol: 'Retour lakaz', fr: 'Accueil', en: 'Back to home' },
  another: { kreol: 'Rapor enn lot fwit', fr: 'Signaler une autre fuite', en: 'Report another leak' },
  notFound: { kreol: 'Pa finn trouv sa kod-la.', fr: 'Code introuvable.', en: 'No report with this code.' },
  area: { kreol: 'Landrwa', fr: 'Secteur', en: 'Area' },
  back: { kreol: 'Retour', fr: 'Retour', en: 'Back' },
};

// App menu, home, area, my reports and learn pages
const T2: Record<string, Record<Lang, string>> = {
  navHome: { kreol: 'Lakaz', fr: 'Accueil', en: 'Home' },
  navArea: { kreol: 'Mo landrwa', fr: 'Mon secteur', en: 'My area' },
  navReport: { kreol: 'Rapor', fr: 'Signaler', en: 'Report' },
  navMine: { kreol: 'Mo rapor', fr: 'Mes signalements', en: 'My reports' },
  navLearn: { kreol: 'Aprann', fr: 'Conseils', en: 'Learn' },

  liveTitle: { kreol: 'Aster lor rezo dlo', fr: 'En ce moment sur le reseau', en: 'Right now on the network' },
  liveAll: { kreol: 'Get tou landrwa', fr: 'Voir tous les secteurs', en: 'See all areas' },
  noNotices: { kreol: 'Pena okenn problem dlo koni aster.', fr: "Aucun probleme d'eau connu en ce moment.", en: 'No known water problems right now.' },
  liveOff: { kreol: 'Pa kapav gagn bann nouvel aster. Ou kapav kontinie avoy rapor.', fr: 'Infos en direct indisponibles. Vous pouvez quand meme signaler.', en: 'Live info is not available right now. You can still report a leak.' },
  checkArea: { kreol: 'Get mo landrwa', fr: 'Voir mon secteur', en: 'Check my area' },

  impactTitle: { kreol: 'Ansam, sa mwa-la', fr: 'Ensemble, ce mois-ci', en: 'Together this month' },
  impReports: { kreol: 'rapor sa semenn-la', fr: 'signalements cette semaine', en: 'reports this week' },
  impFixed: { kreol: 'fwit repare (30 zour)', fr: 'fuites reparees (30 jours)', en: 'leaks fixed (30 days)' },
  impSaved: { kreol: 'm³ par zour ki pa perdi ankor', fr: "m³ par jour qui ne sont plus perdus", en: 'm³ a day no longer lost' },

  myLatest: { kreol: 'Ou dernie rapor', fr: 'Votre dernier signalement', en: 'Your latest report' },
  seeAll: { kreol: 'Get tou', fr: 'Tout voir', en: 'See all' },
  learnTeaserT: { kreol: 'Eski ena enn fwit kot ou?', fr: 'Une fuite chez vous ?', en: 'Is your house leaking?' },
  learnTeaserS: { kreol: 'Enn test 30 minit avek ou konter dlo.', fr: 'Un test de 30 minutes avec votre compteur.', en: 'A 30 minute test with your water meter.' },
  learnCta: { kreol: 'Aprann kouma', fr: 'Voir comment', en: 'Learn how' },

  stNormal: { kreol: 'Normal', fr: 'Normal', en: 'Normal' },
  stChecking: { kreol: 'Pe verifie', fr: 'En verification', en: 'Being checked' },
  stCrew: { kreol: 'Lekip pe travay', fr: 'Equipe sur place', en: 'Crew working' },
  stFixed: { kreol: 'Repare resaman', fr: 'Repare recemment', en: 'Fixed recently' },
  ntChecking: { kreol: 'Nou pe verifie enn kitfwa fwit', fr: 'Nous verifions une possible fuite', en: 'We are checking a possible leak' },
  ntBurst: { kreol: 'Kitfwa enn tiyo finn kase. Presion kapav feb enn moman.', fr: 'Possible rupture de conduite. Pression faible possible.', en: 'Possible burst pipe. Low pressure for a while.' },
  ntCrew: { kreol: 'Enn lekip pe travay', fr: 'Une equipe travaille', en: 'A crew is working' },
  ntCut: { kreol: 'Dlo kapav koupe pou enn ti moman.', fr: "L'eau peut etre coupee un moment.", en: 'Water may be cut for a short time.' },
  ntFixed: { kreol: 'Enn fwit finn repare', fr: 'Une fuite a ete reparee', en: 'A leak was fixed' },
  near: { kreol: 'pre ar', fr: 'pres de', en: 'near' },
  inArea: { kreol: 'dan', fr: 'a', en: 'in' },

  areaTitle: { kreol: 'Dlo dan mo landrwa', fr: "L'eau dans mon secteur", en: 'Water in my area' },
  areaSub: { kreol: 'Bann problem koni lor rezo-la. Mett azour sak detrwa minit.', fr: 'Problemes connus sur le reseau. Mis a jour regulierement.', en: 'Known problems on the network. Updated every few minutes.' },
  updated: { kreol: 'Mett azour', fr: 'Mis a jour', en: 'Updated' },
  savedCopy: { kreol: 'Pa konekte: nou montre dernie info ki nou ti ena.', fr: 'Hors ligne : dernieres infos connues.', en: 'Offline: showing the last info we had.' },
  notInNetwork: { kreol: 'Sa landrwa-la pa ankor dan nou servis.', fr: "Ce secteur n'est pas encore couvert.", en: 'This area is not covered yet.' },

  knownTitle: { kreol: 'Nou deza konn enn problem isi', fr: 'Probleme deja connu ici', en: 'Already known near here' },
  knownCrew: { kreol: 'Enn lekip deza pe travay a', fr: 'Une equipe travaille deja a', en: 'A crew is already working' },
  knownChecking: { kreol: 'Nou deza pe verifie enn problem a', fr: 'Nous verifions deja un probleme a', en: 'We are already checking a problem' },
  fromHere: { kreol: 'm depi isi', fr: "m d'ici", en: 'm from here' },
  knownMore: { kreol: 'Si li paret diferan, avoy ou rapor kanmem. Li ed nou.', fr: 'Si cela semble different, envoyez quand meme. Cela nous aide.', en: 'If it looks different, send your report anyway. It helps us.' },
  outside: { kreol: 'Sa plas-la deor nou servis pou lemoman. Ou kapav avoy li kanmem.', fr: 'Cet endroit est hors de notre zone pour le moment. Vous pouvez quand meme envoyer.', en: 'This spot is outside our service area for now. You can still send it.' },
  step: { kreol: 'Etap', fr: 'Etape', en: 'Step' },

  mineTitle: { kreol: 'Mo rapor', fr: 'Mes signalements', en: 'My reports' },
  mineSub: { kreol: 'Rapor ki finn avoye depi sa telefonn-la.', fr: 'Envoyes depuis ce telephone.', en: 'Reports sent from this phone.' },
  mineEmpty: { kreol: 'Ou pa ankor avoy okenn rapor depi sa telefonn-la.', fr: "Aucun signalement envoye depuis ce telephone.", en: 'You have not sent a report from this phone yet.' },
  lastChecked: { kreol: 'Dernie verifikasion', fr: 'Derniere mise a jour', en: 'Last checked' },
  trackOther: { kreol: 'Swiv enn lot kod', fr: 'Suivre un autre code', en: 'Track another code' },
  unknown: { kreol: 'Pa kone', fr: 'Inconnu', en: 'Unknown' },
  sRec: { kreol: 'Resevwar', fr: 'Recu', en: 'Received' },
  sLinked: { kreol: 'Lie avek enn lalert', fr: 'Lie a une alerte', en: 'Linked to an alert' },
  sCrew: { kreol: 'Lekip pe vini', fr: 'Equipe en route', en: 'Crew on the way' },
  sFixed: { kreol: 'Repare', fr: 'Repare', en: 'Fixed' },
  forget: { kreol: 'Tir lor sa telefonn-la', fr: 'Retirer de ce telephone', en: 'Remove from this phone' },

  learnTitle: { kreol: 'Aprann', fr: 'Conseils', en: 'Learn' },
  l1t: { kreol: 'Verifie si ena fwit kot ou', fr: 'Verifier une fuite chez vous', en: 'Check for a leak at home' },
  l1a: { kreol: 'Ferm tou robine ek pa servi dlo.', fr: "Fermez tous les robinets et n'utilisez pas d'eau.", en: 'Close every tap and do not use any water.' },
  l1b: { kreol: 'Ekrir nimero ki lor ou konter dlo.', fr: 'Notez le chiffre de votre compteur.', en: 'Write down the number on your water meter.' },
  l1c: { kreol: 'Atann 30 minit. Pa tir lasas, pa ouver robine.', fr: "Attendez 30 minutes sans tirer la chasse ni ouvrir de robinet.", en: 'Wait 30 minutes. Do not flush or open a tap.' },
  l1d: { kreol: 'Lir konter-la ankor. Si nimero-la finn bouze, ena enn fwit apre konter-la.', fr: 'Relisez le compteur. Si le chiffre a bouge, il y a une fuite apres le compteur.', en: 'Read the meter again. If the number moved, there is a leak after the meter.' },
  l2t: { kreol: 'Bann sign enn fwit dan lari', fr: "Signes d'une fuite dans la rue", en: 'Signs of a leak in the street' },
  l2a: { kreol: 'Dlo pe koule lor semin san ki lapli finn tonbe', fr: "De l'eau coule sur la route sans pluie", en: 'Water running on the road when it has not rained' },
  l2b: { kreol: 'Enn plas mouye ki pa sek mem kan fer so', fr: 'Une zone humide qui ne seche pas par temps sec', en: 'A wet patch that stays wet in dry weather' },
  l2c: { kreol: 'Presion feb ou pena dlo, me vwazin ena', fr: "Pression faible ou pas d'eau alors que les voisins en ont", en: 'Low pressure or no water when neighbours have water' },
  l2d: { kreol: 'Ou tann dlo pe koule pre ar enn tiyo ou vann', fr: "Bruit d'eau pres d'un tuyau ou d'une vanne", en: 'The sound of running water near a pipe or valve' },
  l3t: { kreol: 'Ki arive apre ou avoy enn rapor', fr: 'Apres votre signalement', en: 'What happens after you report' },
  l3a: { kreol: 'Ou rapor ariv direk kot sant kontrol.', fr: 'Il arrive tout de suite au centre de controle.', en: 'Your report reaches the control room right away.' },
  l3b: { kreol: 'Nou konpar li avek nou bann konter ek lezot rapor pre ar la.', fr: 'Nous le comparons avec nos compteurs et les autres signalements proches.', en: 'We compare it with our meters and other reports nearby.' },
  l3c: { kreol: 'Si se enn fwit, nou avoy enn lekip. Ou kapav swiv li avek ou kod.', fr: 'Si c’est une fuite, une equipe est envoyee. Suivez-la avec votre code.', en: 'If it is a leak, a crew is sent. You can follow it with your code.' },
  l3d: { kreol: 'Kan fwit-la repare, ou rapor montre "Repare".', fr: 'Une fois reparee, votre signalement passe a "Repare".', en: 'When the leak is fixed, your report shows "Fixed".' },
  l4t: { kreol: 'Ekonomiz dlo lakaz', fr: "Economiser l'eau a la maison", en: 'Save water at home' },
  l4a: { kreol: 'Ranz vit bann robine ki pe degout ek twalet ki pe koule.', fr: 'Reparez vite les robinets qui gouttent et les chasses qui coulent.', en: 'Fix dripping taps and running toilets quickly.' },
  l4b: { kreol: 'Ferm robine kan ou pe bros ou ledan.', fr: 'Fermez le robinet en vous brossant les dents.', en: 'Close the tap while brushing your teeth.' },
  l4c: { kreol: 'Aroz plant boner gramatin ou tar aswar.', fr: 'Arrosez tot le matin ou le soir.', en: 'Water plants early morning or in the evening.' },
  l4d: { kreol: 'Ramas dlo lapli pou zardin.', fr: "Recuperez l'eau de pluie pour le jardin.", en: 'Collect rain water for the garden.' },
  emergency: { kreol: 'Gro tiyo kase ou inondasion? Telefonn', fr: 'Grosse fuite ou inondation ? Appelez le', en: 'Big burst or flooding? Call' },
};
Object.assign(T, T2);

@Injectable({ providedIn: 'root' })
export class I18n {
  readonly lang = signal<Lang>(this.saved());

  private saved(): Lang {
    try {
      const l = localStorage.getItem('dlowatch.lang') as Lang | null;
      return l === 'fr' || l === 'en' || l === 'kreol' ? l : DEFAULT_LANG;
    } catch { return DEFAULT_LANG; }
  }

  set(l: Lang) {
    this.lang.set(l);
    try { localStorage.setItem('dlowatch.lang', l); } catch { /* ignore */ }
  }

  t(key: string): string {
    const row = T[key];
    return row ? row[this.lang()] : key;
  }

  /** Short English helper line under Kreol/French text, so everyone can follow. */
  hint(key: string): string {
    return this.lang() === 'en' ? '' : T[key]?.en ?? '';
  }
}
