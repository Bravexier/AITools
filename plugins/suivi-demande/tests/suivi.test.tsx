import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import {
  duree,
  estInstallee,
  estPlusRecente,
  etapesDe,
  installationDe,
  lireCompteur,
  reste,
  texteCompteur,
} from '../hooks/register'

const OUTIL = 'mcp__suivi-demande__suivre'
const PAGE = 'https://claude.ai/code/artifact/00000000-0000-4000-8000-000000000000'
const REGLAGES = JSON.stringify({
  installation: { nom: 'Site de dev', url: 'https://dev.example.test/', motif: '>V(\\d+\\.\\d+\\.\\d+)<' },
})
const PANNEAU = {
  plugin: 'suivi-demande',
  component: 'Pane',
  requestId: 'suivi-demande',
  props: {
    title: 'Suivi de la demande',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

type Monde = {
  /** Session cloud (`CLAUDE_CODE_REMOTE=true`) ou locale. */
  estCloud: boolean
  /** Un panneau se dessine-t-il ? */
  panneau: boolean
  /** Le contenu de `.claude/suivi-demande.json`, s'il existe. */
  reglages?: string
  /** Le seul dossier qui porte ces réglages ; partout quand il n'est pas donné. */
  depot?: string
  /** La version que sert le site d'installation. */
  servie?: () => string
  /** Les fichiers locaux lisibles, par chemin POSIX ; la valeur `undefined` : fichier absent. */
  fichiers?: Record<string, string | undefined>
}

/** Sous Windows, le moteur rend `/home/user/x` en `C:\home\user\x` : on compare la forme POSIX. */
const posix = (chemin: string) => chemin.replace(/\\/g, '/').replace(/^[A-Za-z]:/, '')

/** Tient lieu de moteur sous le plugin, et note ce que le plugin lui demande. */
const monde = (on: On, un: Monde) => {
  const ecritures: Record<string, unknown>[] = []
  const soumis: string[] = []
  const fermes: string[] = []
  const lus: string[] = []
  const temps = mock.clock(on, { now: 1_000_000 })
  mock.store(on)
  on('session.id', () => ({ value: 'session-de-test' }))
  on('session.cwd', () => ({ value: '/depot' }))
  on('session.root', () => ({ value: '/depot' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('env.get', () => ({ value: un.estCloud ? 'true' : undefined }))
  on('fs.read', (_, e) => {
    const chemin = posix(e.path)
    if (un.fichiers !== undefined && chemin in un.fichiers) {
      lus.push(chemin)
      const contenu = un.fichiers[chemin]

      return contenu === undefined ? { deny: 'ENOENT' } : { value: contenu }
    }

    return un.reglages === undefined || (un.depot !== undefined && !chemin.startsWith(`${un.depot}/`))
      ? { deny: 'ENOENT' }
      : { value: un.reglages }
  })
  on('http.fetch', () => ({
    value: { ok: true, status: 200, headers: {}, text: `<span>V${un.servie?.() ?? '0.0.0'}</span>` },
  }))
  on('agent.list', () => ({ value: [] }))
  on('tool.register', () => ({ value: { tool: OUTIL } }))
  on('command.register', () => ({ value: { command: 'suivi' } }))
  on('ui.open', () => ({ value: un.panneau ? { isPlaced: true } : { isPlaced: false, reason: 'aucune surface' } }))
  on('ui.close', (_, e) => {
    fermes.push(e.id)

    return { value: undefined }
  })
  on('ui.toast', () => ({ value: undefined }))
  on('prompt.submit', (_, e) => {
    soumis.push(e.text)

    return { text: e.text }
  })
  on('tool.call', { tool: 'ArtifactData' }, (_, e) => {
    ecritures.push(e as Record<string, unknown>)

    const dit = `committed; now at version ${ecritures.length + 1}`

    return { result: dit, text: dit }
  })
  on('tool.call', { tool: 'mcp__github__merge_pull_request' }, () => ({ result: 'merged', text: 'merged' }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))

  return { ecritures, soumis, fermes, temps, lus }
}

const DEMANDE = {
  tool: OUTIL,
  nouvelle: true,
  but: 'Ajouter un panneau de suivi',
  sous_taches: [
    { titre: 'Écrire le panneau', etat: 'faite', minutes: 20 },
    { titre: 'Le brancher', etat: 'en_cours', minutes: 10, agent: 'tech-front' },
    { titre: 'Le publier', etat: 'programmee', minutes: 5 },
  ],
} as const

const demarrer = ($: Engine) => $.session.start({ cwd: '/depot', surface: 'desktop', isInteractive: true })

/** Ce que l'outil de suivi a répondu à la session. */
const dit = (reponse: { result?: unknown; text?: string | undefined }) => String(reponse.result ?? reponse.text ?? '')

const tour = async ($: Engine, turnId: string, durationMs = 0) => {
  await $.turn.start({ text: 'continue', turnId })
  await $.turn.complete({ answer: '', durationMs, isAborted: false, turnId, reason: 'answer' })
}

test('les durées, les versions, les étapes et les réglages se calculent', () => {
  expect(duree(12)).toBe('12 min')
  expect(duree(65)).toBe('1 h 05')
  expect(estPlusRecente('0.5.34', '0.5.33')).toBe(true)
  expect(estPlusRecente('0.6.0', '0.5.99')).toBe(true)
  expect(estInstallee('0.5.38', '0.5.38')).toBe(true)
  expect(estInstallee('0.5.39', '0.5.38')).toBe(true)
  expect(estInstallee('0.5.37', '0.5.38')).toBe(false)
  expect(
    reste({
      but: 'x',
      debut: 0,
      travailMs: 0,
      tours: 1,
      phase: 'analyse',
      sousTaches: [
        { titre: 'a', etat: 'faite', minutes: 10 },
        { titre: 'b', etat: 'en_cours', minutes: 15 },
        { titre: 'c', etat: 'programmee', minutes: 5 },
      ],
    }),
  ).toBe(20)

  const site = installationDe(REGLAGES)
  expect(etapesDe({ estCloud: false }).map(([cle]) => cle)).toEqual(['analyse', 'developpement', 'verification'])
  expect(etapesDe({ estCloud: true }).map(([cle]) => cle)).toEqual([
    'analyse',
    'developpement',
    'verification',
    'publication',
    'ci',
    'fusion',
  ])
  expect(site?.nom).toBe('Site de dev')
  expect(etapesDe({ estCloud: true, ...(site !== undefined && { installation: site }) }).at(-1)?.[0]).toBe(
    'installation',
  )
  expect(installationDe('{}')).toBeUndefined()
  expect(installationDe('pas du json')).toBeUndefined()
  expect(installationDe(JSON.stringify({ installation: { url: 'https://x.test/', motif: '(' } }))).toBeUndefined()
  expect(installationDe(JSON.stringify({ installation: { url: 'http://x.test/', motif: 'V(.)' } }))).toBeUndefined()
})

test('un agent ne tient pas le suivi : son appel est refusé, celui de la session passe', async ($, on) => {
  // Bug du 2026-11-20 : le hook `tool.offer` n'existe pas sur le moteur 2.1.326,
  // le module ne se chargeait pas du tout.
  monde(on, { estCloud: false, panneau: true })
  await demarrer($)

  const agent = await $.tool.call({ ...DEMANDE, agentId: 'agent-1' })
  expect(agent.deny).toMatch(/session principale/)

  const session = await $.tool.call(DEMANDE)
  expect(session.deny ?? session.isError).toBeUndefined()
})

test('en local, le panneau suffit : ni page, ni GitHub, clôture à la demande', async ($, on) => {
  const { ecritures, soumis, fermes } = monde(on, { estCloud: false, panneau: true })
  await demarrer($)

  const sans = await $.tool.call({ tool: OUTIL, phase: 'developpement' })
  expect(sans.deny ?? sans.isError).toBeTruthy()

  const cree = await $.tool.call({ ...DEMANDE, branche: 'feature/ignoree', pr: 12, ci: 'verte' })
  expect(cree.deny ?? cree.isError).toBeUndefined()
  expect(dit(cree)).toMatch(/panneau affiché, aucune page à publier/)
  expect(dit(cree)).not.toMatch(/aucune page reliée/)

  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANNEAU, surface })

    expect(await ui.find({ type: 'Text', text: /Ajouter un panneau de suivi/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Le brancher \(10 min\) — tech-front/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Reste estimé : environ 15 min/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Vérification/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Publication|GITHUB|feature\/ignoree|PR #12/ })).toBeUndefined()
    await ui.unmount()
  }

  // Une étape de GitHub n'existe pas en local : elle est ignorée.
  const publiee = await $.tool.call({ tool: OUTIL, phase: 'publication' })
  expect(dit(publiee)).toMatch(/phase analyse/)

  const clos = await $.command.run({
    command: 'suivi',
    args: 'clore',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
  const rouvert = await $.command.run({
    command: 'suivi',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
  expect(rouvert.text).toBe('Suivi ouvert.')
  expect(clos.text).toBe('Suivi clos.')
  expect(fermes).toEqual(['suivi-demande'])
  expect(ecritures).toEqual([])
  expect(soumis).toEqual([])

  const ui = await $.ui.mount({ ...PANNEAU, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /Aucune demande suivie/ })).toBeDefined()
  await ui.unmount()
})

test('en cloud, sans panneau : la page est demandée, tenue à jour, et la fusion clôt le suivi', async ($, on) => {
  const { ecritures, soumis } = monde(on, { estCloud: true, panneau: false })
  await demarrer($)

  const cree = await $.tool.call({ ...DEMANDE, branche: 'feature/panneau-de-suivi' })
  expect(dit(cree)).toMatch(/aucune page reliée/)
  expect(ecritures).toEqual([])

  const reliee = await $.tool.call({ tool: OUTIL, page: PAGE, phase: 'ci', pr: 7, ci: 'en_cours' })
  expect(dit(reliee)).toMatch(/page à jour/)
  expect(ecritures).toHaveLength(1)
  const envoye = ecritures[0]?.data as {
    demande: { but: string; branche: string; pr: number; phase: string }
    etapes: [string, string][]
    github: boolean
    installation?: string
  }
  expect(ecritures[0]?.url).toBe(PAGE)
  expect(envoye.demande.branche).toBe('feature/panneau-de-suivi')
  expect(envoye.demande.pr).toBe(7)
  expect(envoye.github).toBe(true)
  expect(envoye.installation).toBeUndefined()
  expect(envoye.etapes.map(([cle]) => cle)).toEqual(['analyse', 'developpement', 'verification', 'publication', 'ci', 'fusion'])

  await $.turn.start({ text: 'fusionne', turnId: 't1' })
  await $.tool.call({ tool: 'mcp__github__merge_pull_request', owner: 'o', repo: 'r', pullNumber: 7 })
  const derniere = ecritures.at(-1)?.data as { demande: unknown; cloture?: { but: string; motif: string } }
  expect(derniere.demande).toBeNull()
  expect(derniere.cloture?.motif).toBe('PR fusionnée')
  // La suppression n'est demandée qu'à la fin du tour : pendant, la demande attendrait le tour lui-même.
  expect(soumis).toEqual([])
  await $.turn.complete({ answer: '', durationMs: 1000, isAborted: false, turnId: 't1', reason: 'answer' })
  expect(soumis).toHaveLength(1)
  expect(soumis[0]).toMatch(/Supprime la page de suivi/)
  expect(soumis[0]).toContain(PAGE)

  // La page est détachée : la demande suivante en redemande une.
  const suivante = await $.tool.call({ ...DEMANDE, but: 'Autre demande' })
  expect(dit(suivante)).toMatch(/aucune page reliée/)
})

test('les réglages suivent le dépôt de la demande, pas le dossier de la session', async ($, on) => {
  let servie = '0.5.44'
  const { ecritures, soumis } = monde(on, {
    estCloud: true,
    panneau: false,
    reglages: REGLAGES,
    depot: '/home/user/maverickcenter',
    servie: () => servie,
  })
  await demarrer($)

  // La session est partie d'un autre dossier : pas d'installation tant que le dépôt n'est pas nommé.
  await $.tool.call({ ...DEMANDE, page: PAGE })
  const sans = ecritures.at(-1)?.data as { installation?: string }
  expect(sans.installation).toBeUndefined()

  await $.tool.call({ tool: OUTIL, depot: '/home/user/maverickcenter' })
  const avec = ecritures.at(-1)?.data as { installation?: string; etapes: [string, string][] }
  expect(avec.installation).toBe('Site de dev')
  expect(avec.etapes.at(-1)?.[0]).toBe('installation')

  // Une fusion faite par le propriétaire se déclare : le suivi attend alors la version, il ne se clôt pas.
  const fusionnee = await $.tool.call({ tool: OUTIL, phase: 'installation', version_cible: '0.5.45' })
  expect(dit(fusionnee)).toMatch(/phase installation/)
  await tour($, 't1')
  expect(soumis).toEqual([])
  servie = '0.5.45'
  await tour($, 't2')
  expect(soumis).toHaveLength(1)
})

test('une PR ouverte dans un dépôt voisin fait lire ses réglages', async ($, on) => {
  const { ecritures } = monde(on, {
    estCloud: true,
    panneau: false,
    reglages: REGLAGES,
    depot: '/home/user/maverickcenter',
  })
  on('tool.call', { tool: 'mcp__github__create_pull_request' }, () => {
    const dit = 'https://github.com/o/MaverickCenter/pull/9'

    return { result: dit, text: dit }
  })
  await demarrer($)
  await $.tool.call({ ...DEMANDE, page: PAGE })

  await $.tool.call({
    tool: 'mcp__github__create_pull_request',
    owner: 'o',
    repo: 'MaverickCenter',
    title: 't',
    head: 'feature/x',
    base: 'dev',
  })
  await tour($, 't1')
  const apres = ecritures.at(-1)?.data as { installation?: string; demande: { pr: number; phase: string } }
  expect(apres.demande.pr).toBe(9)
  expect(apres.installation).toBe('Site de dev')
})

const COMMANDE = { command: 'suivi', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } } as const

test('sans panneau, /suivi mène à la page : il la fait publier, puis donne son adresse', async ($, on) => {
  const { ecritures, soumis, temps } = monde(on, { estCloud: true, panneau: false })
  await demarrer($)

  const sansPage = await $.command.run({ ...COMMANDE, args: '' })
  expect(sansPage.text).toMatch(/en cours de publication/)
  expect(sansPage.text).not.toMatch(/Suivi ouvert/)
  // La demande part au battement suivant, pas depuis la commande.
  expect(soumis).toEqual([])
  await temps.advance(5000)
  expect(soumis).toHaveLength(1)
  expect(soumis[0]).toMatch(/page\/suivi-demande\.html/)

  // La page se relie même sans demande en cours.
  const reliee = await $.tool.call({ tool: OUTIL, page: PAGE })
  expect(dit(reliee)).toMatch(/Page reliée/)
  expect(ecritures).toHaveLength(1)

  const avecPage = await $.command.run({ ...COMMANDE, args: '' })
  expect(avecPage.text).toContain(PAGE)
  expect(soumis).toHaveLength(1)

  // /suivi clore demande lui-même la suppression : la sortie d'une commande ne lance pas de tour.
  await $.tool.call({ ...DEMANDE })
  const clos = await $.command.run({ ...COMMANDE, args: 'clore' })
  expect(clos.text).toMatch(/Suivi clos/)
  await temps.advance(5000)
  expect(soumis).toHaveLength(2)
  expect(soumis[1]).toMatch(/Supprime la page de suivi/)
  expect(soumis[1]).toContain(PAGE)

  // Une page reliée sans demande : /suivi clore la fait supprimer aussi.
  const sansRien = await $.command.run({ ...COMMANDE, args: 'clore' })
  expect(sansRien.text).toMatch(/rien à clore/)
  await $.tool.call({ tool: OUTIL, page: PAGE })
  const pageSeule = await $.command.run({ ...COMMANDE, args: 'clore' })
  expect(pageSeule.text).toMatch(/supprime la page/)
  await temps.advance(5000)
  expect(soumis).toHaveLength(3)
  expect(soumis[2]).toContain(PAGE)
})

test('reprendre la même demande ne remet ni le début ni le temps à zéro', async ($, on) => {
  const { ecritures } = monde(on, { estCloud: true, panneau: false })
  await demarrer($)
  await $.tool.call({ ...DEMANDE, page: PAGE })
  await tour($, 't1', 120_000)

  await $.tool.call({ ...DEMANDE })
  const reprise = ecritures.at(-1)?.data as { demande: { travailMs: number; tours: number; debut: number } }
  expect(reprise.demande.travailMs).toBe(120_000)
  expect(reprise.demande.tours).toBe(2)
  expect(reprise.demande.debut).toBe(1_000_000)

  await $.tool.call({ ...DEMANDE, but: 'Une autre demande' })
  const neuve = ecritures.at(-1)?.data as { demande: { travailMs: number; tours: number } }
  expect(neuve.demande.travailMs).toBe(0)
  expect(neuve.demande.tours).toBe(1)
})

test('un dépôt qui règle son site : la fusion ouvre l’installation, la version cible la clôt', async ($, on) => {
  let servie = '0.5.37'
  const { ecritures, soumis } = monde(on, { estCloud: true, panneau: false, reglages: REGLAGES, servie: () => servie })
  await demarrer($)
  await $.tool.call({ ...DEMANDE, page: PAGE, pr: 7 })

  await $.tool.call({ tool: 'mcp__github__merge_pull_request', owner: 'o', repo: 'r', pullNumber: 7 })
  expect(soumis).toEqual([])

  const sansCible = await $.tool.call({ tool: OUTIL, reste_minutes: 2 })
  expect(dit(sansCible)).toMatch(/phase installation/)
  expect(dit(sansCible)).toMatch(/Donne `version_cible`/)
  const enCours = ecritures.at(-1)?.data as { demande: { versionSite: string }; installation: string; etapes: [string, string][] }
  expect(enCours.installation).toBe('Site de dev')
  expect(enCours.demande.versionSite).toBe('0.5.37')
  expect(enCours.etapes.at(-1)?.[0]).toBe('installation')

  // Une version qui monte sans atteindre la cible ne clôt rien : c'est celle d'une autre PR.
  await $.tool.call({ tool: OUTIL, version_cible: 'v0.5.39' })
  servie = '0.5.38'
  await tour($, 't1')
  expect(soumis).toEqual([])

  servie = '0.5.39'
  await tour($, 't2')
  expect(soumis).toHaveLength(1)
  expect(soumis[0]).toContain(PAGE)
  const derniere = ecritures.at(-1)?.data as { demande: unknown; cloture?: { motif: string } }
  expect(derniere.demande).toBeNull()
  expect(derniere.cloture?.motif).toBe('Site de dev sert V0.5.39')
})

const STATUT = '/atelier/francise/statut.json'
const ENTRAINEMENT = {
  tool: OUTIL,
  nouvelle: true,
  but: 'Entraîner la voix francisée',
  sous_taches: [
    {
      titre: 'Entraîner le modèle',
      etat: 'en_cours',
      compteur: { fichier: STATUT, cle: 'epoque', total_cle: 'max_epoques', libelle: 'Époque', decalage: 1 },
    },
  ],
} as const
const statut = (epoque: number, fini = false) =>
  JSON.stringify({ epoque, max_epoques: 40, duree_epoque_s: 1500, fini })

/** Ce que le panneau affiche sur la sous-tâche du compteur. */
const ligneDuCompteur = async ($: Engine) => {
  const ui = await $.ui.mount({ ...PANNEAU, surface: 'desktop' })
  const ligne = await ui.find({ type: 'Text', text: /Entraîner le modèle/ })
  const vue = ligne === undefined ? '' : JSON.stringify(ligne)
  await ui.unmount()

  return vue
}

/** Le compteur de la dernière écriture de la page. */
const compteurDeLaPage = (ecritures: Record<string, unknown>[]) =>
  (ecritures.at(-1)?.data as { demande: { sousTaches: { compteur?: { valeur?: number; total?: number } }[] } })
    .demande.sousTaches[0]?.compteur

test('un compteur se lit dans un fichier JSON, décalage compris, sans dépasser le total', () => {
  const reglage = { fichier: STATUT, cle: 'epoque', totalCle: 'max_epoques', libelle: 'Époque', decalage: 1 }
  expect(lireCompteur(statut(6), reglage)).toEqual({ valeur: 7, total: 40 })
  // À la fin de l'entraînement, le fichier donne le total lui-même : 40/40, pas 41/40.
  expect(lireCompteur(statut(40, true), reglage)).toEqual({ valeur: 40, total: 40 })
  expect(lireCompteur(statut(6), { fichier: STATUT, cle: 'epoque' })).toEqual({ valeur: 6 })
  expect(lireCompteur('{"epoque": "six"}', reglage)).toBeUndefined()
  expect(lireCompteur('{"autre": 3}', reglage)).toBeUndefined()
  expect(lireCompteur('{"epoque": 6, "max_ep', reglage)).toBeUndefined()
  expect(lireCompteur('null', reglage)).toBeUndefined()
  expect(lireCompteur('', reglage)).toBeUndefined()

  expect(texteCompteur({ ...reglage, valeur: 7, total: 40 })).toBe('Époque 7/40')
  expect(texteCompteur({ fichier: STATUT, cle: 'lot', valeur: 3 })).toBe('lot 3')
  expect(texteCompteur(reglage)).toBe('Époque : fichier pas encore lu')
})

test('le compteur se relit seul une fois par minute, sans tour de la session, panneau et page compris', async ($, on) => {
  const fichiers: Record<string, string | undefined> = { [STATUT]: statut(6) }
  const { ecritures, temps, lus } = monde(on, { estCloud: false, panneau: true, fichiers })
  await demarrer($)

  const cree = await $.tool.call({ ...ENTRAINEMENT, page: PAGE })
  expect(dit(cree)).toMatch(/panneau affiché ; page à jour/)
  expect(lus).toHaveLength(1)
  expect(await ligneDuCompteur($)).toMatch(/Époque 7\/40/)
  expect(compteurDeLaPage(ecritures)).toMatchObject({ valeur: 7, total: 40 })

  // L'époque avance pendant que la session est inactive : aucun tour ne commence.
  fichiers[STATUT] = statut(7)
  await temps.advance(5000)
  expect(lus).toHaveLength(1)
  expect(await ligneDuCompteur($)).toMatch(/Époque 7\/40/)

  await temps.advance(55_000)
  expect(lus).toHaveLength(2)
  expect(await ligneDuCompteur($)).toMatch(/Époque 8\/40/)
  expect(compteurDeLaPage(ecritures)).toMatchObject({ valeur: 8, total: 40 })

  // Une valeur qui n'a pas bougé ne réécrit pas la page.
  const avant = ecritures.length
  await temps.advance(60_000)
  expect(lus).toHaveLength(3)
  expect(ecritures).toHaveLength(avant)
})

test('un fichier absent ou un JSON invalide ne casse rien : la dernière valeur reste', async ($, on) => {
  const fichiers: Record<string, string | undefined> = { [STATUT]: undefined }
  const { ecritures, temps, lus } = monde(on, { estCloud: false, panneau: true, fichiers })
  await demarrer($)

  // Le fichier n'existe pas encore : l'entraînement n'a pas commencé.
  const cree = await $.tool.call({ ...ENTRAINEMENT, page: PAGE })
  expect(cree.deny ?? cree.isError).toBeUndefined()
  expect(await ligneDuCompteur($)).toMatch(/Époque : fichier pas encore lu/)

  fichiers[STATUT] = statut(2)
  await temps.advance(60_000)
  expect(await ligneDuCompteur($)).toMatch(/Époque 3\/40/)

  // Écriture coupée en plein milieu, puis fichier retiré : la valeur lue reste.
  fichiers[STATUT] = '{"epoque": 3, "max_ep'
  await temps.advance(60_000)
  expect(await ligneDuCompteur($)).toMatch(/Époque 3\/40/)
  fichiers[STATUT] = undefined
  await temps.advance(60_000)
  expect(lus).toHaveLength(4)
  expect(await ligneDuCompteur($)).toMatch(/Époque 3\/40/)
  expect(compteurDeLaPage(ecritures)).toMatchObject({ valeur: 3, total: 40 })

  // La session redonne ses sous-tâches avant la lecture suivante : le compteur garde sa valeur.
  await $.tool.call({ tool: OUTIL, sous_taches: ENTRAINEMENT.sous_taches, reste_minutes: 600 })
  expect(lus).toHaveLength(4)
  expect(await ligneDuCompteur($)).toMatch(/Époque 3\/40/)
  expect(compteurDeLaPage(ecritures)).toMatchObject({ valeur: 3, total: 40 })
})
