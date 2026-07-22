import {describe,expect,it} from 'vitest'
import {CAMPAIGN_PRESETS} from './campaignPresets'

const limits:Record<string,number>={
  name:200,
  topic:380,
  content_objective:3000,
  primary_keyword:200,
  research_subject:1000,
  segment:200,
  reader_context:5000,
  reader_life_stage:200,
  reader_goal:3000,
  commercial_objective:3000,
  offer:3000,
  desired_action:1000,
  additional_context:20000,
  reader_start_state:1000,
  reader_final_state:1000,
  article_promise:3000,
  scope_limit:2000,
}

describe('campaign presets',()=>{
  it('keeps every configured field inside the active form contract',()=>{
    for(const preset of CAMPAIGN_PRESETS){
      expect(preset.values).not.toHaveProperty('jurisdiction')
      for(const [field,limit] of Object.entries(limits)){
        const value=preset.values[field]
        if(typeof value==='string') expect(value.length,`${preset.id}.${field}`).toBeLessThanOrEqual(limit)
      }
    }
  })
})
