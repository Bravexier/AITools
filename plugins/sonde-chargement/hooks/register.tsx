import type { Register } from 'claude-code'

const PANNEAU = 'sonde-chargement'
const OUTIL = 'mcp__sonde-chargement__sonde'

// Sonde provisoire. Elle répond à deux questions avant d'écrire le vrai
// plugin : le module de hooks d'un plugin installé se charge-t-il dans une
// session neuve, et un panneau s'y dessine-t-il ?
export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'sonde',
      description:
        'Sonde de chargement du marketplace AITools. Dit si le module de hooks est chargé, quelles surfaces sont attachées et si un panneau est affiché.',
      inputSchema: { type: 'object', properties: {} },
      isDeferred: false,
    })

    return next(e)
  })

  on('tool.run', { tool: OUTIL }, async $ => {
    const place = await $.ui.open({ id: PANNEAU, title: 'Sonde AITools' })
    const surfaces = await $.session.surfaces()

    return {
      result: `SONDE-CHARGEE-7391 ; surfaces : ${surfaces.join(', ') || 'aucune'} ; panneau affiché : ${place.isPlaced ? 'oui' : 'non'}.`,
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANNEAU }, ($, e) => {
    const { Box, Text } = $.ui.resolve(e)

    return (
      <Box flexDirection="column">
        <Text>Sonde AITools : le panneau s'affiche sur la surface « {e.surface} ».</Text>
      </Box>
    )
  })
}
