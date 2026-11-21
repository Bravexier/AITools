import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Agent, Cadre, Compteur, Demande, Etat, Installation, Page, Phase, SousTache } from '../types'

const MOD = 'suivi-demande'
/** Le panneau texte des versions 1.1 et avant : refermé s'il reste ouvert, le suivi n'est plus que la page. */
const ANCIEN_PANNEAU = 'suivi-demande'
const OUTIL = 'mcp__suivi-demande__suivre'
/** Le fichier par lequel un dépôt règle le suivi (aujourd'hui : son site d'installation). */
const REGLAGES = '.claude/suivi-demande.json'

// Changer l'étiquette quand la forme d'une valeur change : elle écarte ce
// qu'une version précédente a rangé, qui survit à un rechargement.
const suivi = atom({ plugin: 'suivi-demande', key: 'suivi' } as const, { demande: null }, { shape: 'suivi-3' })
const agents = atom({ plugin: 'suivi-demande', key: 'agents' } as const, [], { shape: 'agents-1' })
const principal = atom(
  { plugin: 'suivi-demande', key: 'principal' } as const,
  { estActif: false, attendReponse: false },
  { shape: 'principal-2' },
)
const page = atom({ plugin: 'suivi-demande', key: 'page' } as const, {}, { shape: 'page-2' })
const cadre = atom({ plugin: 'suivi-demande', key: 'cadre' } as const, { estCloud: false }, { shape: 'cadre-1' })

const ETATS: readonly Etat[] = ['faite', 'en_cours', 'programmee', 'en_attente']

type Etape = readonly [Phase, string]

/** Les étapes affichées : GitHub seulement en cloud, l'installation seulement si le dépôt la règle. */
export const etapesDe = (un: Cadre): readonly Etape[] => [
  ['analyse', 'Analyse'],
  ['developpement', 'Développement'],
  ['verification', 'Vérification'],
  ...(un.estCloud ? ([['publication', 'Publication'], ['ci', 'CI'], ['fusion', 'Fusion']] as const) : []),
  ...(un.installation !== undefined ? ([['installation', 'Installation']] as const) : []),
]

const consigne = (un: Cadre, racine: string) =>
  [
    '## Suivi de la demande',
    `Le propriétaire suit la demande en cours dans une page publiée, tenue par l'outil ${OUTIL}. Une demande qui ne change aucun fichier (question, analyse en lecture) n'ouvre pas de suivi.`,
    "- Au premier message d'une demande : `nouvelle: true`, le but en une phrase, les sous-tâches prévues et la durée estimée de chacune en minutes.",
    `- Quand il poursuit la demande, puis à chaque changement d'état d'une sous-tâche${un.estCloud ? ', de phase, de branche, de PR ou de CI' : ' ou de phase'} : seulement les champs qui changent (\`sous_taches\` se donne toujours en entier).`,
    '- Quand tu attends sa réponse ou son accord : `attente`, en une phrase ; une chaîne vide quand ce n’est plus le cas.',
    "- Un appel par changement, groupé avec tes autres appels d'outils ; ne commente pas le suivi dans tes réponses.",
    `- Si le résultat de l'outil dit « aucune page reliée » : lis puis publie telle quelle ${racine}/page/suivi-demande.html (outil Artifact, \`capabilities: {"db": {}, "user": {}}\`, icône \`activity\` ; la page est déjà conçue), puis donne son adresse dans \`page\`.`,
    ...(un.estCloud && un.installation === undefined
      ? [
          '- Ta propre fusion de la PR clôt le suivi sans toi. Si la PR est fusionnée autrement (événement GitHub, fusion par le propriétaire) : `phase: "terminee"`.',
        ]
      : []),
    ...(un.estCloud
      ? [
          `- Si la demande modifie un autre dépôt que le dossier de la session : \`depot\`, son chemin absolu, dès le premier appel. Ses réglages (${REGLAGES}) y sont lus : ils peuvent ajouter une étape d'installation.`,
        ]
      : []),
    ...(un.installation !== undefined
      ? [
          `- Après la fusion (la tienne est constatée sans toi ; une fusion faite autrement se déclare par \`phase: "installation"\`) : \`version_cible\`, la version attendue sur ${un.installation.nom}.${un.installation.consigne !== undefined ? ` ${un.installation.consigne}` : ''} Le suivi se clôt quand cette version est servie. Entre deux tours rien ne tourne : programme un rappel (\`send_later\`) pour revenir le constater.`,
        ]
      : []),
    ...(un.estCloud
      ? []
      : ['- Ne clos pas le suivi de toi-même : le propriétaire le clôt par `/suivi clore`, ou te le demande (`phase: "terminee"`).']),
    "- Quand l'outil ou le mod te donne une page à supprimer, supprime-la (Artifact, action delete ; le propriétaire confirme dans l'application).",
    `Le temps passé et les agents${un.estCloud ? ", l'ouverture et la fusion d'une PR" : ''} sont suivis sans toi.`,
  ].join('\n')

const schema = (un: Cadre) => ({
  type: 'object',
  properties: {
    nouvelle: {
      type: 'boolean',
      description: 'true pour une nouvelle demande : remplace le suivi en cours. Le même but reprend le suivi sans remettre le temps à zéro.',
    },
    but: { type: 'string', description: 'Le but de la demande, en une phrase.' },
    sous_taches: {
      type: 'array',
      description: 'La liste entière des sous-tâches, dans leur ordre.',
      items: {
        type: 'object',
        properties: {
          titre: { type: 'string' },
          etat: { type: 'string', enum: ETATS },
          minutes: { type: 'number', description: 'Durée estimée, en minutes.' },
          agent: { type: 'string', description: "L'agent qui la porte, s'il y en a un." },
          compteur: {
            type: 'object',
            description:
              "Avancement qu'un programme écrit dans un fichier JSON local (époque d'un entraînement, lot d'un traitement) : le mod le relit seul une fois par minute et l'affiche sur la sous-tâche, même session inactive. Redonne-le à chaque appel qui redonne la sous-tâche.",
            properties: {
              fichier: { type: 'string', description: 'Chemin absolu du fichier JSON.' },
              cle: { type: 'string', description: 'Clé de la valeur, au premier niveau du JSON (un nombre).' },
              total_cle: { type: 'string', description: 'Clé du total, au premier niveau du JSON.' },
              libelle: { type: 'string', description: 'Libellé affiché devant la valeur, par exemple « Époque ».' },
              decalage: { type: 'number', description: 'Ajouté à la valeur lue : 1 quand le fichier compte à partir de 0.' },
            },
            required: ['fichier', 'cle'],
          },
        },
        required: ['titre', 'etat'],
      },
    },
    reste_minutes: { type: 'number', description: 'Reste à faire estimé, en minutes.' },
    phase: {
      type: 'string',
      // En cloud, l'étape d'installation est toujours proposée : les réglages d'un dépôt peuvent arriver après l'enregistrement de l'outil.
      enum: [...new Set([...etapesDe(un).map(([cle]) => cle), ...(un.estCloud ? ['installation'] : []), 'terminee'])],
      description: '`terminee` clôt le suivi. `installation` n’existe que si le dépôt règle un site d’installation.',
    },
    ...(un.estCloud && {
      branche: { type: 'string' },
      pr: { type: 'number', description: 'Numéro de la PR.' },
      ci: { type: 'string', enum: ['en_cours', 'verte', 'rouge'] },
    }),
    ...(un.estCloud && {
      depot: {
        type: 'string',
        description: 'Chemin absolu du dépôt que la demande modifie, quand ce n’est pas le dossier de la session.',
      },
      version_cible: {
        type: 'string',
        description:
          'Version attendue sur le site d’installation pour cette demande, par exemple 1.4.2. Sans effet si le dépôt ne règle pas de site.',
      },
    }),
    attente: { type: 'string', description: 'Ce qui est attendu du propriétaire ; vide pour lever l’attente.' },
    page: { type: 'string', description: 'Adresse claude.ai de la page publiée qui affiche le suivi.' },
  },
})

const texte = (valeur: unknown) =>
  typeof valeur === 'string' && valeur.trim() !== '' ? valeur.trim() : undefined

const nombre = (valeur: unknown) =>
  typeof valeur === 'number' && Number.isFinite(valeur) && valeur >= 0 ? valeur : undefined

const compteurDe = (valeur: unknown): Compteur | undefined => {
  if (typeof valeur !== 'object' || valeur === null) return undefined
  const brut = valeur as Record<string, unknown>
  const fichier = texte(brut.fichier)
  const cle = texte(brut.cle)
  if (fichier === undefined || cle === undefined) return undefined
  const totalCle = texte(brut.total_cle)
  const libelle = texte(brut.libelle)
  const decalage =
    typeof brut.decalage === 'number' && Number.isFinite(brut.decalage) && brut.decalage !== 0 ? brut.decalage : undefined

  return {
    fichier,
    cle,
    ...(totalCle !== undefined && { totalCle }),
    ...(libelle !== undefined && { libelle }),
    ...(decalage !== undefined && { decalage }),
  }
}

const sousTaches = (valeur: unknown): SousTache[] | undefined => {
  if (!Array.isArray(valeur)) return undefined

  return valeur.flatMap((brut: unknown): SousTache[] => {
    if (typeof brut !== 'object' || brut === null) return []
    const une = brut as Record<string, unknown>
    const titre = texte(une.titre)
    if (titre === undefined) return []
    const etat = ETATS.find(connu => connu === une.etat) ?? 'programmee'
    const minutes = nombre(une.minutes)
    const agent = texte(une.agent)
    const compteur = compteurDe(une.compteur)

    return [
      {
        titre,
        etat,
        ...(minutes !== undefined && { minutes }),
        ...(agent !== undefined && { agent }),
        ...(compteur !== undefined && { compteur }),
      },
    ]
  })
}

/** Lit un compteur dans le contenu de son fichier ; rien si le JSON est invalide ou n'a pas la valeur. */
export const lireCompteur = (contenu: string, compteur: Compteur): { valeur: number; total?: number } | undefined => {
  let donnees: unknown
  try {
    donnees = JSON.parse(contenu)
  } catch {
    return undefined
  }
  if (typeof donnees !== 'object' || donnees === null) return undefined
  const champs = donnees as Record<string, unknown>
  const brute = champs[compteur.cle]
  if (typeof brute !== 'number' || !Number.isFinite(brute)) return undefined
  const total = compteur.totalCle === undefined ? undefined : nombre(champs[compteur.totalCle])
  const valeur = brute + (compteur.decalage ?? 0)

  // Un fichier qui compte à partir de 0 donne, à la fin, le total lui-même : le décalage ne le dépasse pas.
  return total === undefined ? { valeur } : { valeur: Math.min(valeur, total), total }
}

const memeLecture = (a: Compteur, b: Compteur) =>
  a.fichier === b.fichier && a.cle === b.cle && a.totalCle === b.totalCle && a.decalage === b.decalage

/** Une sous-tâche redonnée garde la dernière lecture de son compteur, s'il lit toujours la même chose. */
const garderLesLectures = (liste: SousTache[] | undefined, avant: readonly SousTache[] = []) =>
  liste?.map(une => {
    const compteur = une.compteur
    if (compteur === undefined) return une
    const lu = avant.find(autre => autre.compteur !== undefined && memeLecture(autre.compteur, compteur))?.compteur
    if (lu?.valeur === undefined) return une

    return { ...une, compteur: { ...compteur, valeur: lu.valeur, ...(lu.total !== undefined && { total: lu.total }) } }
  })

export const estPlusRecente = (version: string, reference: string) => {
  const a = version.split('.').map(Number)
  const b = reference.split('.').map(Number)
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) > (b[i] ?? 0)
  }

  return false
}

/** La demande est installée quand le site sert sa version, ou une plus récente : une version voisine plus ancienne ne compte pas. */
export const estInstallee = (versionServie: string, versionCible: string) => !estPlusRecente(versionCible, versionServie)

/** Lit le bloc `installation` des réglages d'un dépôt ; rien si le fichier ne le donne pas en entier. */
export const installationDe = (contenu: string): Installation | undefined => {
  try {
    const bloc = (JSON.parse(contenu) as { installation?: Record<string, unknown> }).installation
    const url = texte(bloc?.url)
    const motif = texte(bloc?.motif)
    if (url === undefined || motif === undefined || !url.startsWith('https://')) return undefined
    new RegExp(motif)
    const note = texte(bloc?.consigne)

    return { nom: texte(bloc?.nom) ?? 'le site', url, motif, ...(note !== undefined && { consigne: note }) }
  } catch {
    return undefined
  }
}

/** Où tourne la session et ce que le dépôt règle. Sans GitHub (session locale), pas d'installation non plus. */
const etablirLeCadre = async ($: EngineInterface, depots: readonly (string | undefined)[] = []) => {
  const estCloud = (await $.env.get('CLAUDE_CODE_REMOTE').catch(() => undefined)) === 'true'
  let installation: Installation | undefined
  if (estCloud) {
    // Le dépôt de la demande d'abord, puis le dossier de la session.
    const dossiers = [
      ...depots,
      await $.session.cwd().catch(() => undefined),
      await $.session.root().catch(() => undefined),
    ]
    for (const dossier of dossiers) {
      if (dossier === undefined || installation !== undefined) continue
      installation = await $.fs
        .read(`${dossier}/${REGLAGES}`)
        .then(installationDe)
        .catch(() => undefined)
    }
  }
  const etabli: Cadre = { estCloud, ...(installation !== undefined && { installation }) }
  await update($, cadre, () => etabli)

  return etabli
}

const versionServie = async ($: EngineInterface, site: Installation) => {
  try {
    const reponse = await $.http.fetch(site.url)

    return reponse.ok ? new RegExp(site.motif).exec(reponse.text)?.[1] : undefined
  } catch {
    return undefined
  }
}

const modifier = ($: EngineInterface, changer: (demande: Demande) => Demande) =>
  update($, suivi, etat => (etat.demande === null ? etat : { demande: changer(etat.demande) }))

const releverLesAgents = async ($: EngineInterface) => {
  const liste = await $.agent.list()
  const releve: Agent[] = liste.map(un => ({
    id: un.id,
    type: un.type,
    description: un.description,
    statut: un.status,
    debut: un.startTime,
    ...(un.parentId !== undefined && { parentId: un.parentId }),
  }))
  const avant = await read($, agents)
  if (JSON.stringify(avant) !== JSON.stringify(releve)) await update($, agents, () => releve)
}

/** Un fichier de compteur n'est pas relu avant ce délai. */
const RELECTURE_MS = 60_000
const derniereLecture = new Map<string, number>()

/**
 * Relit les fichiers des compteurs, chacun au plus une fois par minute. Un
 * fichier absent ou illisible ne change rien : la dernière valeur reste.
 */
const releverLesCompteurs = async ($: EngineInterface) => {
  const { demande } = await read($, suivi)
  if (demande === null) return
  const maintenant = await $.clock.now()
  const fichiers = [...new Set(demande.sousTaches.flatMap(une => (une.compteur === undefined ? [] : [une.compteur.fichier])))]
  const contenus = new Map<string, string>()
  for (const fichier of fichiers) {
    if (maintenant - (derniereLecture.get(fichier) ?? -Infinity) < RELECTURE_MS) continue
    derniereLecture.set(fichier, maintenant)
    const contenu = await $.fs.read(fichier).catch(() => undefined)
    if (typeof contenu === 'string') contenus.set(fichier, contenu)
  }
  if (contenus.size === 0) return

  const relire = (une: SousTache): SousTache => {
    const { compteur } = une
    const contenu = compteur === undefined ? undefined : contenus.get(compteur.fichier)
    if (compteur === undefined || contenu === undefined) return une
    const lu = lireCompteur(contenu, compteur)
    if (lu === undefined || (lu.valeur === compteur.valeur && lu.total === compteur.total)) return une
    const { valeur: _avant, total: _ancien, ...reglage } = compteur

    return { ...une, compteur: { ...reglage, ...lu } }
  }
  // Rien n'est écrit si aucune valeur n'a bougé : la page n'est pas réécrite pour rien.
  const { demande: actuelle } = await read($, suivi)
  if (actuelle !== null && actuelle.sousTaches.some(une => relire(une) !== une)) {
    await modifier($, une => ({ ...une, sousTaches: une.sousTaches.map(relire) }))
  }
}

const cleDeSession = async ($: EngineInterface) => `suivi:${await $.session.id()}`
let derniereSauvegarde = ''

/** Garde le suivi au-delà d'un redémarrage de la session : `$.state` ne survit qu'à un rechargement du mod. */
const sauver = async ($: EngineInterface) => {
  const garde = { demande: (await read($, suivi)).demande, page: await read($, page) }
  const empreinte = JSON.stringify(garde)
  if (empreinte === derniereSauvegarde) return
  const rienAGarder = garde.demande === null && garde.page.url === undefined && garde.page.aSupprimer === undefined
  if (rienAGarder) await $.store.delete(await cleDeSession($))
  else await $.store.set(await cleDeSession($), garde)
  derniereSauvegarde = empreinte
}

const restaurer = async ($: EngineInterface) => {
  if ((await read($, suivi)).demande !== null) return
  const garde = await $.store.get(await cleDeSession($))
  if (typeof garde !== 'object' || garde === null) return
  const { demande, page: cible } = garde as { demande?: Demande | null; page?: Page }
  const adresse = cible?.url
  const aSupprimer = cible?.aSupprimer
  // Une demande gardée par une version d'une autre forme n'est pas reprise.
  if (demande !== undefined && demande !== null && typeof demande.tours === 'number') {
    await update($, suivi, () => ({ demande }))
  }
  // La version du document n'est pas reprise : la première écriture la redécouvre.
  if (adresse !== undefined) await update($, page, () => ({ url: adresse }))
  else if (aSupprimer !== undefined) await update($, page, () => ({ aSupprimer }))
}

type Cloture = { but: string; motif: string; le: number }

let envoiEnCours = false
let dernierEnvoi = ''

/** Écrit l'état dans la base de la page publiée ; rend ce qu'il faut en dire. */
const pousser = async ($: EngineInterface, cloture?: Cloture): Promise<string> => {
  const cible = await read($, page)
  if (cible.url === undefined) return 'aucune page reliée'
  if (envoiEnCours) return 'envoi déjà en cours'
  const un = await read($, cadre)
  const contenu = {
    demande: (await read($, suivi)).demande,
    agents: await read($, agents),
    principal: await read($, principal),
    etapes: etapesDe(un),
    github: un.estCloud,
    ...(un.installation !== undefined && { installation: un.installation.nom }),
    ...(cloture !== undefined && { cloture }),
  }
  const empreinte = JSON.stringify(contenu)
  if (empreinte === dernierEnvoi) return 'page à jour'
  envoiEnCours = true
  try {
    const ecrire = (version: number | undefined) =>
      $.tool.call({
        tool: 'ArtifactData',
        action: 'set',
        url: cible.url,
        collection: 'suivi',
        doc_id: 'etat',
        data: { ...contenu, majLe: Date.now() },
        ...(version !== undefined && { if_version: version }),
      })
    const lire = (reponse: { deny?: string; text?: string }) => reponse.deny ?? reponse.text ?? ''
    // Sans version connue, on en tente une : le refus nomme la version courante.
    let reponse = lire(await ecrire(cible.version ?? 1))
    if (!/committed/.test(reponse)) {
      const actuelle = /now at version (\d+)/.exec(reponse)?.[1]
      reponse = lire(await ecrire(actuelle === undefined ? undefined : Number(actuelle)))
    }
    if (!/committed/.test(reponse)) return `page NON mise à jour : ${reponse.slice(0, 300)}`
    const version = /now at version (\d+)/.exec(reponse)?.[1]
    await update($, page, une => ({ ...une, ...(version !== undefined && { version: Number(version) }) }))
    dernierEnvoi = empreinte

    return 'page à jour'
  } finally {
    envoiEnCours = false
  }
}

/** Relève ce qui a bougé et l'écrit : appelé au battement, et aux bornes de chaque tour, où une session cloud est sûre d'être éveillée. */
const mettreAJour = async ($: EngineInterface) => {
  await releverLesAgents($)
  await pousser($)
  await sauver($)
}

const demandeDeSuppression = (adresse: string) =>
  `[suivi-demande] Suivi clos. Supprime la page de suivi ${adresse} (Artifact, action delete ; le propriétaire confirme dans l'application) et préviens-le en une phrase.`

/**
 * Clôt le suivi : plus de demande, page marquée terminée puis
 * détachée. La page reste à supprimer par la session : `direct` rend la
 * demande de suppression à qui peut la lui dire tout de suite (le résultat
 * d'un outil, d'une commande) ; sinon elle attend `annoncer`.
 */
const cloturer = async ($: EngineInterface, motif: string, direct = false): Promise<string | undefined> => {
  const { demande } = await read($, suivi)
  if (demande === null) return undefined
  const cible = await read($, page)
  await update($, suivi, () => ({ demande: null }))
  $.ui.toast(`Suivi clos : ${motif}.`)
  await pousser($, { but: demande.but, motif, le: await $.clock.now() })
  const adresse = cible.url
  await update($, page, () => (adresse === undefined || direct ? {} : { aSupprimer: adresse }))
  await sauver($)

  return adresse === undefined ? undefined : demandeDeSuppression(adresse)
}

/** Ce que le mod doit encore demander à la session, hors de tout hook qui retient un tour. */
const aDemander: string[] = []

/**
 * Remet à la session ce qui l'attend : la page d'un suivi clos à supprimer,
 * une page à publier. Appelé à la fin d'un tour et au battement, jamais depuis
 * un hook `tool.call`, `turn.start` ou `command.run` : la demande attendrait
 * le tour que ce hook retient.
 */
const annoncer = async ($: EngineInterface) => {
  const { aSupprimer } = await read($, page)
  if (aSupprimer !== undefined) {
    await update($, page, () => ({}))
    await sauver($)
    aDemander.push(demandeDeSuppression(aSupprimer))
  }
  for (let text = aDemander.shift(); text !== undefined; text = aDemander.shift()) {
    await $.prompt.submit({ text })
  }
}

const entrerEnInstallation = async ($: EngineInterface, site: Installation) => {
  const version = await versionServie($, site)
  await modifier($, demande => ({
    ...demande,
    phase: 'installation',
    ...(version !== undefined && { versionSite: version }),
  }))
}

/** Clôt quand le site sert la version cible. Jamais sur un signal indirect : sans version cible, rien n'est constaté. */
const guetterLInstallation = async ($: EngineInterface) => {
  const site = (await read($, cadre)).installation
  const { demande } = await read($, suivi)
  if (site === undefined || demande === null || demande.phase !== 'installation') return
  const version = await versionServie($, site)
  if (version === undefined) return
  if (demande.versionCible !== undefined && estInstallee(version, demande.versionCible)) {
    await cloturer($, `${site.nom} sert V${version}`)
  } else if (demande.versionSite !== version) {
    await modifier($, une => ({ ...une, versionSite: version }))
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await restaurer($)
    const un = await etablirLeCadre($, [(await read($, suivi)).demande?.depot])
    await $.tool.register({
      name: 'suivre',
      description:
        'Tient le suivi de la demande du propriétaire (page publiée). À appeler au début de chaque demande qui change des fichiers (`nouvelle`, `but`, `sous_taches`), puis à chaque changement : sous-tâches et durées estimées, reste à faire, phase, attente du propriétaire. Seuls les champs donnés changent.',
      inputSchema: schema(un),
      isDeferred: false,
    })
    await $.command.register({
      name: 'suivi',
      description: 'Donner la page de suivi de la demande ; `/suivi clore` le clôt',
      argumentHint: '[clore]',
    })
    // Le suivi n'est plus que la page : un panneau texte resté d'une version précédente se referme.
    void $.ui.close({ id: ANCIEN_PANNEAU }).catch(() => undefined)

    let battement = 0
    $.clock.every(5000, () => {
      battement += 1
      void (async () => {
        await releverLesCompteurs($)
        await mettreAJour($)
        if (battement % 12 === 0) await guetterLInstallation($)
        // Hors d'un tour seulement : pendant un tour, c'est `turn.complete` qui annonce.
        if (!(await read($, principal)).estActif) await annoncer($)
      })().catch(() => undefined)
    })

    return next(e)
  })

  on('command.run', { command: 'suivi' }, async ($, e) => {
    // Ce que la commande laisse à demander part juste après elle, tant que la session est éveillée.
    $.clock.after(300, () => void annoncer($).catch(() => undefined))
    if (e.args.trim().toLowerCase() === 'clore') {
      if ((await read($, suivi)).demande === null) {
        // Une page reliée sans demande : clore, c'est la faire supprimer.
        const reste = (await read($, page)).url
        if (reste === undefined) return { text: 'Aucune demande suivie : rien à clore.' }
        await update($, page, () => ({ aSupprimer: reste }))
        await sauver($)

        return { text: 'Aucune demande suivie. La session supprime la page de suivi : confirmez dans l’application.' }
      }
      // La sortie d'une commande ne lance pas de tour : la suppression de la page est demandée au battement suivant.
      const suite = await cloturer($, 'à votre demande')

      return { text: suite === undefined ? 'Suivi clos.' : 'Suivi clos. La session supprime sa page : confirmez dans l’application.' }
    }
    const adresse = (await read($, page)).url
    if (adresse !== undefined) return { text: `Le suivi est sur la page ${adresse}` }
    aDemander.push(
      `[suivi-demande] Le propriétaire demande à voir le suivi et aucune page n'est reliée. Lis puis publie telle quelle ${$.plugin.root}/page/suivi-demande.html (outil Artifact, \`capabilities: {"db": {}, "user": {}}\`, icône \`activity\`), donne son adresse à l'outil ${OUTIL} dans \`page\`, puis donne-lui le lien en une phrase.`,
    )

    return { text: 'La page de suivi est en cours de publication.' }
  })

  on('prompt.compose', async ($, e, next) => {
    const compose = await next(e)
    // La consigne ne va qu'à qui a l'outil : la session principale, pas ses agents.
    if (!e.tools.some(nom => nom.endsWith('__suivre'))) return compose
    const text = consigne(await read($, cadre), $.plugin.root)

    return { ...compose, sections: [...compose.sections, { id: MOD, text, scope: 'session' }] }
  })

  // Seule la session principale tient le suivi : l'appel d'un agent est refusé.
  // (Le moteur 2.1.326 n'a pas d'événement pour cacher un outil à un seul agent :
  // `tool.list` règle l'outillage de toute la session.)
  on('tool.call', { tool: OUTIL }, ($, e, next) =>
    e.agentId !== undefined ? { deny: 'Seule la session principale tient le suivi de la demande.' } : next(e),
  )

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      const debutTour = await $.clock.now()
      await update($, principal, etat => ({ ...etat, estActif: true, debutTour }))
      await modifier($, demande => ({ ...demande, tours: demande.tours + 1 }))
      await guetterLInstallation($).catch(() => undefined)
    }
    void mettreAJour($).catch(() => undefined)

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) {
      await update($, principal, etat => ({ attendReponse: etat.attendReponse, estActif: false }))
      await modifier($, demande => ({ ...demande, travailMs: demande.travailMs + e.durationMs }))
      // Dernière écriture avant que la session s'endorme : elle est attendue.
      await mettreAJour($).catch(() => undefined)
    } else {
      void releverLesAgents($).catch(() => undefined)
    }
    const fini = await next(e)
    if (e.agentId === undefined) await annoncer($).catch(() => undefined)

    return fini
  })

  on('agent.spawn', async ($, e, next) => {
    const lance = await next(e)
    void releverLesAgents($).catch(() => undefined)

    return lance
  })

  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    await update($, principal, etat => ({ ...etat, attendReponse: true }))
    try {
      return await next(e)
    } finally {
      await update($, principal, etat => ({ ...etat, attendReponse: false }))
    }
  })

  on('tool.call', { tool: 'mcp__github__create_pull_request' }, async ($, e, next) => {
    const cree = await next(e)
    const numero = /\/pull\/(\d+)/.exec(cree.text ?? '')?.[1]
    if (cree.deny === undefined && cree.isError !== true && numero !== undefined && (await read($, cadre)).estCloud) {
      await modifier($, demande => ({ ...demande, pr: Number(numero), phase: 'ci', ci: 'en_cours' }))
      // Une PR ouverte dans un dépôt cloné à côté du dossier de la session : ses réglages sont cherchés là.
      const depot = texte((e as { repo?: unknown }).repo)
      const { demande } = await read($, suivi)
      if (depot !== undefined && demande !== null && demande.depot === undefined) {
        const parents = [await $.session.root().catch(() => undefined), '/home/user']
        const voisins = parents.flatMap(parent =>
          parent === undefined ? [] : [`${parent}/${depot}`, `${parent}/${depot.toLowerCase()}`],
        )
        await etablirLeCadre($, voisins)
      }
    }

    return cree
  })

  on('tool.call', { tool: 'mcp__github__merge_pull_request' }, async ($, e, next) => {
    const fusionne = await next(e)
    const un = await read($, cadre)
    if (fusionne.deny !== undefined || fusionne.isError === true || !un.estCloud) return fusionne
    if (un.installation !== undefined) {
      await entrerEnInstallation($, un.installation)
    } else {
      await cloturer($, 'PR fusionnée')
    }

    return fusionne
  })

  on('tool.run', { tool: OUTIL }, async ($, e) => {
    const maintenant = await $.clock.now()
    const avant = (await read($, suivi)).demande
    const estCloud = (await read($, cadre)).estCloud
    const depot = estCloud ? (texte(e.depot) ?? (e.nouvelle === true ? undefined : avant?.depot)) : undefined
    // Les réglages suivent le dépôt de la demande : relus quand il est donné, et à chaque nouvelle demande.
    const un =
      texte(e.depot) !== undefined || e.nouvelle === true ? await etablirLeCadre($, [depot]) : await read($, cadre)
    const butDonne = texte(e.but)
    // `nouvelle` avec le même but rétablit le suivi : ni le début ni le temps mesuré ne repartent de zéro.
    const base = e.nouvelle === true && butDonne !== undefined && butDonne !== avant?.but ? null : avant
    const but = butDonne ?? base?.but
    if (but === undefined) {
      // Relier une page sans demande : elle affiche les agents, et attend la prochaine demande.
      const seule = texte(e.page)
      if (seule === undefined) return { deny: 'Donne `but` : aucune demande n’est suivie pour l’instant.' }
      await update($, page, () => ({ url: seule }))
      const envoi = await pousser($)
      await sauver($)

      return { result: `Page reliée, aucune demande suivie pour l’instant ; ${envoi}.` }
    }

    const liste = garderLesLectures(sousTaches(e.sous_taches), avant?.sousTaches)
    const resteMinutes = nombre(e.reste_minutes)
    const phase =
      e.phase === 'terminee' ? ('terminee' as const) : etapesDe(un).find(([cle]) => cle === e.phase)?.[0]
    const branche = un.estCloud ? texte(e.branche) : undefined
    const pr = un.estCloud ? nombre(e.pr) : undefined
    const ci = un.estCloud ? (['en_cours', 'verte', 'rouge'] as const).find(connu => connu === e.ci) : undefined
    const versionCible =
      un.installation === undefined ? undefined : /^[vV]?(\d+(?:\.\d+)+)$/.exec(texte(e.version_cible) ?? '')?.[1]
    const depart: Demande = base ?? {
      but,
      debut: maintenant,
      travailMs: 0,
      tours: 1,
      sousTaches: [],
      phase: 'analyse',
    }
    const adresse = texte(e.page)
    if (adresse !== undefined) await update($, page, () => ({ url: adresse }))
    const { attente: _levee, ...sansAttente } = depart
    const attente = typeof e.attente === 'string' ? texte(e.attente) : depart.attente
    const demande: Demande = {
      ...sansAttente,
      but,
      ...(liste !== undefined && { sousTaches: liste }),
      ...(resteMinutes !== undefined && { resteMinutes }),
      ...(depot !== undefined && { depot }),
      ...(phase !== undefined && { phase }),
      ...(branche !== undefined && { branche }),
      ...(pr !== undefined && { pr }),
      ...(versionCible !== undefined && { versionCible }),
      ...(ci !== undefined && { ci }),
      ...(attente !== undefined && { attente }),
    }

    if (demande.phase === 'terminee') {
      await update($, suivi, () => ({ demande }))
      const suite = await cloturer($, 'demande terminée', true)

      return { result: suite ?? 'Suivi clos.' }
    }
    await update($, suivi, () => ({ demande }))
    // Un compteur nouveau est lu tout de suite ; les autres gardent leur rythme d'une lecture par minute.
    await releverLesCompteurs($).catch(() => undefined)
    if (un.installation !== undefined && demande.phase === 'installation' && avant?.phase !== 'installation') {
      await entrerEnInstallation($, un.installation)
    }
    // Le suivi ne s'affiche que dans la page : sans page reliée, la session la publie (voir la consigne).
    const envoi = await pousser($)
    await sauver($)

    const faites = demande.sousTaches.filter(une => une.etat === 'faite').length
    const manque =
      demande.phase === 'installation' && demande.versionCible === undefined
        ? ' Donne `version_cible` pour que l’installation soit constatée.'
        : ''

    return {
      result: `Suivi à jour : ${faites}/${demande.sousTaches.length} sous-tâches faites, phase ${demande.phase} ; ${envoi}.${manque}`,
    }
  })
}
