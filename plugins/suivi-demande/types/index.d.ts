export type Etat = 'faite' | 'en_cours' | 'programmee' | 'en_attente'

/**
 * Un compteur que le mod relit seul dans un fichier JSON local, au plus une
 * fois par minute : l'avancement d'une sous-tâche qui tourne sans la session
 * (époque d'un entraînement, lot d'un traitement).
 */
export type Compteur = {
  /** Chemin absolu du fichier JSON. */
  fichier: string
  /** Clé de la valeur, au premier niveau du JSON. */
  cle: string
  /** Clé du total, au premier niveau du JSON. */
  totalCle?: string
  /** Libellé affiché devant la valeur ; la clé à défaut. */
  libelle?: string
  /** Ajouté à la valeur lue : 1 quand le fichier compte à partir de 0. */
  decalage?: number
  /** Dernière valeur lue, décalage compris, jamais au-delà du total. Gardée quand le fichier manque ou est illisible. */
  valeur?: number
  /** Dernier total lu. */
  total?: number
}

export type SousTache = {
  titre: string
  etat: Etat
  /** Durée estimée de la sous-tâche, en minutes. */
  minutes?: number
  /** L'agent qui la porte, s'il y en a un. */
  agent?: string
  compteur?: Compteur
}

/**
 * Les étapes d'une demande. `publication`, `ci` et `fusion` n'existent qu'en
 * session cloud ; `installation` seulement quand le dépôt la règle. `terminee`
 * n'est pas affichée : la déclarer clôt le suivi.
 */
export type Phase =
  | 'analyse'
  | 'developpement'
  | 'verification'
  | 'publication'
  | 'ci'
  | 'fusion'
  | 'installation'
  | 'terminee'

export type Demande = {
  but: string
  /** Premier message de la demande, en millisecondes. */
  debut: number
  /** Temps de travail mesuré de la session principale. */
  travailMs: number
  /** Tours de travail de la session principale : messages, rappels et événements compris. */
  tours: number
  sousTaches: SousTache[]
  /** Reste à faire estimé par la session principale, en minutes. */
  resteMinutes?: number
  /** Le dossier du dépôt que la demande modifie, quand ce n'est pas celui de la session : ses réglages y sont lus. */
  depot?: string
  phase: Phase
  branche?: string
  pr?: number
  ci?: 'en_cours' | 'verte' | 'rouge'
  /** Ce qui est attendu du propriétaire. */
  attente?: string
  /** Version attendue sur le site d'installation : la demande est installée quand il la sert. */
  versionCible?: string
  versionSite?: string
}

export type Suivi = { demande: Demande | null }

export type Agent = {
  id: string
  type: string
  description: string
  statut: string
  parentId?: string
  debut: number
}

export type Principal = { estActif: boolean; attendReponse: boolean; debutTour?: number }

/**
 * La page publiée qui affiche le suivi, et la version de son document.
 * `aSupprimer` : la page d'un suivi clos, que la session doit encore supprimer.
 */
export type Page = { url?: string; version?: number; aSupprimer?: string }

/** Le site où une demande fusionnée s'installe, réglé par le dépôt dans `.claude/suivi-demande.json`. */
export type Installation = {
  /** Le nom affiché, par exemple « Site de dev ». */
  nom: string
  /** L'adresse dont la page porte la version servie. */
  url: string
  /** Expression régulière dont le premier groupe capture la version servie. */
  motif: string
  /** Ce que la session doit savoir pour donner `version_cible`. */
  consigne?: string
}

/** Où tourne la session, et ce que le dépôt a réglé : décide des étapes et des blocs affichés. */
export type Cadre = { estCloud: boolean; installation?: Installation }

declare module 'claude-code' {
  interface PluginState {
    'suivi-demande': {
      suivi: Shaped<Suivi>
      agents: Shaped<Agent[]>
      principal: Shaped<Principal>
      page: Shaped<Page>
      cadre: Shaped<Cadre>
      horloge: number
    }
  }
}
