// Walmart reviewer notifier - Consumption-plan Function App, Table Storage,
// Key Vault. Sized to sit inside the Functions free grant; see docs/cost.md.

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short lowercase prefix used to name resources.')
@minLength(3)
@maxLength(11)
param namePrefix string = 'wmrev'

@description('Push provider: ntfy, pushover or telegram.')
@allowed(['ntfy', 'pushover', 'telegram'])
param notifyProvider string = 'ntfy'

@description('NCRONTAB schedule for the poller. Default: every 2 minutes.')
param pollSchedule string = '0 */2 * * * *'

@description('Secrets seeded into Key Vault. Supply via a parameter file or -p on the CLI.')
@secure()
param imapPassword string = ''
@secure()
param notifySecret string = ''
@secure()
param ingestToken string = ''

// The ntfy topic name IS the credential -- anyone who knows it can read your
// alerts -- so it is @secure() like the rest. Non-secure parameters are stored
// in plaintext in the deployment history and readable from the portal forever.
@secure()
param ntfyTopic string = ''

@description('Non-secret notifier settings.')
param imapHost string = ''
param imapUser string = ''

@description('Record items as seen without alerting. Run one cycle like this against a fresh dedupe table, then set it false.')
param seedMode bool = false

@description('Deploy Application Insights. Adds cost beyond the 5 GB/month free grant.')
param enableAppInsights bool = true

var suffix = uniqueString(resourceGroup().id)
var storageName = toLower('${namePrefix}st${substring(suffix, 0, 8)}')
var functionAppName = '${namePrefix}-func-${substring(suffix, 0, 6)}'
var keyVaultName = '${namePrefix}-kv-${substring(suffix, 0, 6)}'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    accessTier: 'Hot'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan-${substring(suffix, 0, 6)}'
  location: location
  // Y1/Dynamic is the Consumption plan: pay-per-execution with a monthly free
  // grant of 1M executions and 400,000 GB-seconds.
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true } // Linux
}

resource insights 'Microsoft.Insights/components@2020-02-02' = if (enableAppInsights) {
  name: '${namePrefix}-ai-${substring(suffix, 0, 6)}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    IngestionMode: 'ApplicationInsights'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource imapSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(imapPassword)) {
  parent: vault
  name: 'imap-password'
  properties: { value: imapPassword }
}

resource notifySecretRes 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(notifySecret)) {
  parent: vault
  name: 'notify-secret'
  properties: { value: notifySecret }
}

resource ingestTokenRes 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(ingestToken)) {
  parent: vault
  name: 'ingest-token'
  properties: { value: ingestToken }
}

resource ntfyTopicRes 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(ntfyTopic)) {
  parent: vault
  name: 'ntfy-topic'
  properties: { value: ntfyTopic }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  // Secret URIs are built as strings rather than resource references (ARM's
  // if() evaluates both branches, so referencing a conditionally-created
  // secret fails when it is not deployed), which means the dependency on the
  // secrets has to be declared by hand.
  dependsOn: [imapSecret, ingestTokenRes, notifySecretRes, ntfyTopicRes]
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(
        [
          {
            name: 'AzureWebJobsStorage'
            value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
          }
          { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
          { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
          { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
          { name: 'POLL_SCHEDULE', value: pollSchedule }
          { name: 'NOTIFY_PROVIDER', value: notifyProvider }
          { name: 'IMAP_HOST', value: imapHost }
          { name: 'IMAP_USER', value: imapUser }
          { name: 'SEED_MODE', value: string(seedMode) }
        ],
        empty(ntfyTopic) ? [] : [
          {
            name: 'NTFY_TOPIC'
            value: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/ntfy-topic)'
          }
        ],
        empty(imapPassword) ? [] : [
          {
            name: 'IMAP_PASSWORD'
            value: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/imap-password)'
          }
        ],
        empty(ingestToken) ? [] : [
          {
            name: 'INGEST_TOKEN'
            value: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/ingest-token)'
          }
        ],
        empty(notifySecret) ? [] : [
          {
            // Maps to PUSHOVER_TOKEN / TELEGRAM_BOT_TOKEN / NTFY_TOKEN depending
            // on the chosen provider.
            name: notifyProvider == 'pushover' ? 'PUSHOVER_TOKEN' : (notifyProvider == 'telegram' ? 'TELEGRAM_BOT_TOKEN' : 'NTFY_TOKEN')
            value: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/notify-secret)'
          }
        ],
        !enableAppInsights ? [] : [
          {
            name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
            // insights! : the branch is already guarded by enableAppInsights,
            // but Bicep cannot narrow a conditional resource on its own.
            value: insights!.properties.ConnectionString
          }
        ]
      )
    }
  }
}

// Key Vault Secrets User - lets the app resolve @Microsoft.KeyVault(...) references.
var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, functionApp.id, secretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output functionAppHost string = functionApp.properties.defaultHostName
output keyVaultName string = vault.name
output healthUrl string = 'https://${functionApp.properties.defaultHostName}/api/health'
output ingestUrl string = 'https://${functionApp.properties.defaultHostName}/api/ingest'
