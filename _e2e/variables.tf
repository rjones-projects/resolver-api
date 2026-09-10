variable "project_id" {
  description = "The GCP project ID."
  type        = string
  default     = ""

  # NOTE: No upstream default — set this before applying.
}

variable "region" {
  description = "The GCP region for resources."
  type        = string
  default     = "us-central1"
}

variable "allow_dns_egress" {
  description = "Allow egress traffic to DNS servers (port 53)"
  type        = bool
  default     = true
}

variable "allow_github_access" {
  description = "If true, creates a firewall rule to allow egress traffic to Vodafone GitHub IPs on port 443."
  type        = bool
  default     = true
}

variable "allow_internal_communication" {
  description = "Value for allow_internal_communication."
  type        = bool
  default     = true
}

variable "allow_metadata_server_egress" {
  description = "Allow egress to GCP Metadata server (169.254.169.254)"
  type        = bool
  default     = true
}

variable "bucket_default" {
  description = "A bucket object to be merged into."
  type        = object({
    bucket_name                 = string
    location                    = string
    storage_class               = string
    uniform_bucket_level_access = bool
    kms_key_name                = string
    labels                      = map(string)
    versioning_enabled          = bool
    public_access_prevention    = string
    accesses = list(object({
      role    = string
      members = list(string)
    }))
    retention_policy = object({
      is_locked        = bool
      retention_period = number
    })
    logging = object({
      log_bucket        = string
      log_object_prefix = string
    })
    lifecycle_rules = list(object({
      action = map(string)
      condition = object({
        age                   = number
        with_state            = string
        created_before        = string
        matches_storage_class = list(string)
        num_newer_versions    = number
      })
    }))
    autoclass = bool
    iam_bindings = map(object({
      members = list(string)
      role    = string
      condition = optional(object({
        expression  = string
        title       = string
        description = optional(string)
      }))

    }))
    iam_bindings_additive = map(object({
      member = string
      role   = string
      condition = optional(object({
        expression  = string
        title       = string
        description = optional(string)
      }))
    }))
    objects_to_upload = map(object({
      name           = string
      source         = optional(string)
      detect_md5hash = optional(string)
    }))
  })
  default     = {
    bucket_name                 = null
    location                    = null
    storage_class               = "STANDARD"
    uniform_bucket_level_access = true
    kms_key_name                = null
    labels                      = {}
    versioning_enabled          = true
    public_access_prevention    = "enforced"
    accesses                    = []
    retention_policy            = null
    logging                     = null
    lifecycle_rules             = []
    autoclass                   = true
    iam_bindings                = {}
    iam_bindings_additive       = {}
    objects_to_upload           = {}
  }
}

variable "common_resource_id" {
  description = "A common string to use as a prefix for resource names. If not provided, the project name is used."
  type        = string
  default     = null
}

variable "context" {
  description = "Context-specific interpolations."
  type        = object({
    cidr_ranges      = optional(map(string), {})
    cidr_ranges_sets = optional(map(list(string)), {})
    iam_principals   = optional(map(string), {})
    networks         = optional(map(string), {})
    project_ids      = optional(map(string), {})
  })
  default     = {}
}

variable "create_googleapis_dns" {
  description = "Create Cloud DNS private zones for googleapis.com, gcr.io, and pkg.dev"
  type        = bool
  default     = true
}

variable "create_nat" {
  description = "If false, do not create Cloud NAT or NAT external IPs."
  type        = bool
  default     = true
}

variable "custom_allow_github_fw_name" {
  description = "Value for custom_allow_github_fw_name."
  type        = string
  default     = "egress-allow-vf-github"
}

variable "custom_allow_internal_communication_fw_name" {
  description = "Value for custom_allow_internal_communication_fw_name."
  type        = string
  default     = "egress-allow-internal-commn"
}

variable "custom_allow_private_google_apis_fw_name" {
  description = "Value for custom_allow_private_google_apis_fw_name."
  type        = string
  default     = "allow-private-googleapis-egress"
}

variable "custom_allow_restricted_google_apis_fw_name" {
  description = "Value for custom_allow_restricted_google_apis_fw_name."
  type        = string
  default     = "allow-restricted-googleapis-egress"
}

variable "custom_deny_egress_fw_name" {
  description = "Value for custom_deny_egress_fw_name."
  type        = string
  default     = "deny-egress"
}

variable "custom_nat_ip_desc" {
  description = "A custom description for the Cloud NAT external IP address."
  type        = string
  default     = ""
}

variable "custom_nat_ip_name" {
  description = "A custom name for the Cloud NAT external IP address. If not provided, a name will be generated."
  type        = string
  default     = ""
}

variable "custom_nat_name" {
  description = "A custom name for the Cloud NAT gateway. If not provided, a name will be generated."
  type        = string
  default     = ""
}

variable "custom_router_name" {
  description = "A custom name for the Cloud Router. If not provided, a name will be generated."
  type        = string
  default     = ""
}

variable "custom_vpc_name" {
  description = "A custom name for the VPC network. If not provided, a name will be generated."
  type        = string
  default     = ""
}

variable "default_rules_config" {
  description = "Optionally created convenience rules. Set the 'disabled' attribute to true, or individual rule attributes to empty lists to disable."
  type        = object({
    admin_ranges = optional(list(string))
    disabled     = optional(bool, false)
    http_ranges = optional(list(string), [
      "35.191.0.0/16", "130.211.0.0/22", "209.85.152.0/22", "209.85.204.0/22"]
    )
    http_tags = optional(list(string), ["http-server"])
    https_ranges = optional(list(string), [
      "35.191.0.0/16", "130.211.0.0/22", "209.85.152.0/22", "209.85.204.0/22"]
    )
    https_tags = optional(list(string), ["https-server"])
    ssh_ranges = optional(list(string), ["35.235.240.0/20"])
    ssh_tags   = optional(list(string), ["ssh"])
  })
  default     = {}
}

variable "deny_egress" {
  description = "Warning: Deny egress to 0.0.0.0/0 does not work with transparent Squid."
  type        = bool
  default     = false
}

variable "description" {
  description = "Description for the VPC network."
  type        = string
  default     = null
}

variable "dns" {
  description = "DNS config with specs"
  type        = any
  default     = ""
}

variable "dns_default" {
  description = "A dns object to be merged into"
  type        = object({
    name = string
    zone_config = object({
      domain = string
      forwarding = optional(object({
        forwarders      = optional(map(string))
        client_networks = list(string)
      }))
      peering = optional(object({
        client_networks = list(string)
        peer_network    = string
      }))
      public = optional(object({
        dnssec_config = optional(object({
          non_existence = optional(string)
          state         = string
          key_signing_key = optional(object(
            { algorithm = string, key_length = number })
          )
          zone_signing_key = optional(object(
            { algorithm = string, key_length = number })
          )
        }))
        enable_logging = optional(bool)
      }))
      private = optional(object({
        client_networks             = list(string)
        service_directory_namespace = optional(string)
        reverse_managed             = optional(bool)
      }))
    })
    description   = string
    force_destroy = bool
    iam           = map(list(string))
    recordsets = map(object({
      ttl     = optional(number)
      records = optional(list(string))
      geo_routing = optional(list(object({
        location = string
        records  = optional(list(string))
        health_checked_targets = optional(list(object({
          load_balancer_type = string
          ip_address         = string
          port               = string
          ip_protocol        = string
          network_url        = string
          project            = string
          region             = optional(string)
        })))
      })))
      wrr_routing = optional(list(object({
        weight  = number
        records = list(string)
      })))
    }))
    labels = map(string)
  })
  default     = {
    name = null
    zone_config = {
      domain = null
    }
    description   = null
    force_destroy = false
    iam           = {}
    recordsets    = {}
    labels        = {}
  }
}

variable "egress_rules" {
  description = "List of egress rule definitions, default to deny action. Null destination ranges will be replaced with 0/0."
  type        = map(object({
    deny               = optional(bool, true)
    description        = optional(string)
    destination_ranges = optional(list(string))
    destination_fqdns  = optional(list(string)) # Added
    disabled           = optional(bool, false)
    enable_logging = optional(object({
      include_metadata = optional(bool)
    }))
    priority             = optional(number, 1000)
    source_ranges        = optional(list(string))
    source_fqdns         = optional(list(string)) # Added
    targets              = optional(list(string))
    use_service_accounts = optional(bool, false)
    rules = optional(list(object({
      protocol = string
      ports    = optional(list(string))
    })), [{ protocol = "all" }])
  }))
  default     = {}
}

variable "enable_private_service_connect" {
  description = "Value for enable_private_service_connect."
  type        = bool
  default     = true
}

variable "export_custom_routes" {
  description = "Export custom routes on the servicenetworking peering (PSC). Set true when peered networks need custom route export."
  type        = bool
  default     = true
}

variable "export_subnet_routes_with_public_ip" {
  description = "Export subnet routes with public IP on the servicenetworking peering (PSC)."
  type        = bool
  default     = false
}

variable "external_global_address" {
  description = "External global address configuration from project.yaml. Must contain a 'spec' list of address definitions."
  type        = any
  default     = {
    spec = []
  }
}

variable "external_global_loadbalancer" {
  description = "External global load balancer configuration from project.yaml. Must contain a 'spec' list of load balancer definitions."
  type        = any
  default     = {
    spec = []
  }
}

variable "external_subnets_allows_nats" {
  description = "A list of subnetworks allowed for NAT configuration."
  type        = list(object({
    self_link = string
  }))
  default     = []
}

variable "factories_config" {
  description = "Paths to data files and folders that enable factory functionality."
  type        = object({
    cidr_tpl_file = optional(string)
    rules_folder  = optional(string)
  })
  default     = {}
}

variable "firewall" {
  description = "Firewall module config with spec."
  type        = any
  default     = null
}

variable "gcs" {
  description = "GCS config with specification."
  type        = any
  default     = null
}

variable "global_address_name" {
  description = "The name of the global internal address for Private Service Connect."
  type        = string
  default     = "private-ip-address"
}

variable "googleapis_dns_mode" {
  description = "Which VIP to use for googleapis.com: RESTRICTED (199.36.153.4/30) or PRIVATE (199.36.153.8/30)"
  type        = string
  default     = "PRIVATE"
}

variable "iam_custom_role_stack" {
  description = "iam_custom_role_stack object"
  type        = any
  default     = null
}

variable "iam_custom_role_stack_default" {
  description = "A iam_custom_role_stack object to be merged into"
  type        = object({
    target_project_ids     = set(string)
    role_id                = string
    title                  = string
    description            = string
    core_permissions_count = number
    resolve_base_roles     = bool
    base_roles             = list(string)
    additional_permissions = list(string)
    excluded_permissions   = list(string)
    members                = list(string)
    stage                  = string
  })
  default     = {
    target_project_ids     = []
    role_id                = null
    title                  = ""
    description            = ""
    core_permissions_count = 1500
    resolve_base_roles     = true
    base_roles = [
      "roles/bigquery.admin",
      "roles/cloudbuild.admin",
      "roles/cloudfunctions.admin",
      "roles/composer.admin",
      "roles/compute.admin",
      "roles/container.admin",
      "roles/dataform.admin",
      "roles/dataproc.admin",
      "roles/dns.admin",
      "roles/run.admin",
      "roles/compute.networkAdmin",
      "roles/iam.serviceAccountAdmin",
      "roles/cloudkms.admin",
      "roles/logging.admin",
      "roles/monitoring.admin",
      "roles/pubsub.admin",
      "roles/secretmanager.admin",
      "roles/cloudsql.admin",
      "roles/storage.admin",
      "roles/iam.admin"
    ]
    additional_permissions = []
    excluded_permissions = [
      "compute.firewallPolicies.copyRules",
      "compute.firewallPolicies.move",
      "compute.securityPolicies.copyRules",
      "compute.securityPolicies.move",
      "compute.orgRolloutPlans.create",
      "compute.orgRolloutPlans.delete",
      "compute.orgRolloutPlans.get",
      "compute.orgRolloutPlans.list",
      "compute.orgRolloutPlans.update",
      "compute.orgRolloutPlans.use",
      "compute.orgRolloutPlans.view",
      "compute.orgRolloutPlans.viewAuditTrail",
      "compute.orgRollouts.cancel",
      "compute.orgRollouts.create",
      "compute.orgRollouts.delete",
      "compute.orgRollouts.get",
      "compute.orgRollouts.list",
      "compute.orgRollouts.update",
      "compute.orgRollouts.use",
      "compute.orgRollouts.view",
      "compute.orgRollouts.viewAuditTrail",
      "compute.orgRollouts.pause",
      "compute.orgRollouts.resume",
      "compute.orgRollouts.rollback",
      "compute.orgRollouts.start",
      "compute.orgRollouts.stop",
      "compute.orgRollouts.suspend",
      "compute.orgRollouts.unpause",
      "compute.orgRollouts.unroll",
      "compute.orgRollouts.unsuspend",
      "compute.orgRollouts.unstop",
      "stackdriver.projects.edit",
      "resourcemanager.projects.list",
      "servicenetworking.services.deletePeering",
      "compute.securityPolicies.removeAssociation",
      "eventarc.multiProjectSources.collectGoogleApiEvents",
      "compute.securityPolicies.addAssociation",
      "compute.oslogin.updateExternalUser",
      "iam.googleapis.com/workforcePoolProviderKeys.create",
      "iam.googleapis.com/workforcePoolProviderKeys.delete",
      "iam.googleapis.com/workforcePoolProviderKeys.get",
      "iam.googleapis.com/workforcePoolProviderKeys.list",
      "iam.googleapis.com/workforcePoolProviderKeys.undelete",
      "iam.googleapis.com/workforcePoolProviderScimGroups.create",
      "iam.googleapis.com/workforcePoolProviderScimGroups.delete",
      "iam.googleapis.com/workforcePoolProviderScimGroups.get",
      "iam.googleapis.com/workforcePoolProviderScimGroups.list",
      "iam.googleapis.com/workforcePoolProviderScimGroups.patch",
      "iam.googleapis.com/workforcePoolProviderScimGroups.put",
      "iam.googleapis.com/workforcePoolProviderScimUsers.create",
      "iam.googleapis.com/workforcePoolProviderScimUsers.delete",
      "iam.googleapis.com/workforcePoolProviderScimUsers.get",
      "iam.googleapis.com/workforcePoolProviderScimUsers.list",
      "iam.googleapis.com/workforcePoolProviderScimUsers.patch",
      "iam.googleapis.com/workforcePoolProviderScimUsers.put",
      "iam.googleapis.com/workforcePoolProviders.computeUserAttributes",
      "iam.googleapis.com/workforcePoolProviders.create",
      "iam.googleapis.com/workforcePoolProviders.delete",
      "iam.googleapis.com/workforcePoolProviders.get",
      "iam.googleapis.com/workforcePoolProviders.list",
      "iam.googleapis.com/workforcePoolProviders.undelete",
      "iam.googleapis.com/workforcePoolProviders.update",
      "iam.googleapis.com/workforcePoolSubjects.delete",
      "iam.googleapis.com/workforcePoolSubjects.undelete",
      "iam.googleapis.com/workforcePools.create",
      "iam.googleapis.com/workforcePools.createPolicyBinding",
      "iam.googleapis.com/workforcePools.delete",
      "iam.googleapis.com/workforcePools.deletePolicyBinding",
      "iam.googleapis.com/workforcePools.get",
      "iam.googleapis.com/workforcePools.getIamPolicy",
      "iam.googleapis.com/workforcePools.list",
      "iam.googleapis.com/workforcePools.searchPolicyBindings",
      "iam.googleapis.com/workforcePools.setIamPolicy",
      "iam.googleapis.com/workforcePools.undelete",
      "iam.googleapis.com/workforcePools.update",
      "iam.googleapis.com/workforcePools.updatePolicyBinding",
      "iam.googleapis.com/workspacePools.createPolicyBinding",
      "iam.googleapis.com/workspacePools.deletePolicyBinding",
      "iam.googleapis.com/workspacePools.searchPolicyBindings",
      "iam.googleapis.com/workspacePools.updatePolicyBinding"
    ]
    members = []
    stage   = "GA"
  }
}

variable "iam_service_account" {
  description = "Service account config with items"
  type        = object({
    spec = optional(list(object({
      project_id                 = optional(string)
      name                       = optional(string)
      display_name               = optional(string)
      description                = optional(string)
      prefix                     = optional(string)
      service_account_reuse      = optional(bool)
      tag_bindings               = optional(map(string))
      iam_bindings               = optional(map(list(string)))
      iam_billing_roles          = optional(map(list(string)))
      iam_by_principles_additive = optional(map(list(string)))
      iam_by_principles          = optional(map(list(string)))
      iam_folder_roles           = optional(map(list(string)))
      iam_organization_roles     = optional(map(list(string)))
      iam_project_roles          = optional(list(string))
      iam_sa_roles               = optional(map(list(string)))
      iam_storage_roles          = optional(map(list(string)))
    })), [])
  })
  default     = null
}

variable "import_custom_routes" {
  description = "Import custom routes on the servicenetworking peering (PSC)."
  type        = bool
  default     = false
}

variable "import_subnet_routes_with_public_ip" {
  description = "Import subnet routes with public IP on the servicenetworking peering (PSC)."
  type        = bool
  default     = false
}

variable "ingress_health_check" {
  description = "If true, creates a firewall rule to allow ingress traffic from Google Cloud health checkers."
  type        = bool
  default     = true
}

variable "ingress_rules" {
  description = "List of ingress rule definitions, default to allow action. Null source ranges will be replaced with 0/0."
  type        = map(object({
    deny               = optional(bool, false)
    description        = optional(string)
    destination_ranges = optional(list(string), [])
    destination_fqdns  = optional(list(string)) # Added
    disabled           = optional(bool, false)
    enable_logging = optional(object({
      include_metadata = optional(bool)
    }))
    priority             = optional(number, 1000)
    source_ranges        = optional(list(string))
    source_fqdns         = optional(list(string)) # Added
    sources              = optional(list(string))
    targets              = optional(list(string))
    use_service_accounts = optional(bool, false)
    rules = optional(list(object({
      protocol = string
      ports    = optional(list(string))
    })), [{ protocol = "all" }])
  }))
  default     = {}
}

variable "ingress_ssh_via_IAP" {
  description = "If true, creates a firewall rule to allow SSH ingress traffic via Google Cloud's Identity-Aware Proxy."
  type        = bool
  default     = true
}

variable "min_ports_per_vm" {
  description = "Minimum number of ports per VM"
  type        = number
  default     = 64
}

variable "named_ranges" {
  description = "Define mapping of names to ranges that can be used in custom rules."
  type        = map(list(string))
  default     = {
    any            = ["0.0.0.0/0"]
    dns-forwarders = ["35.199.192.0/19"]
    health-checkers = [
      "35.191.0.0/16", "130.211.0.0/22", "209.85.152.0/22", "209.85.204.0/22"
    ]
    iap-forwarders        = ["35.235.240.0/20"]
    private-googleapis    = ["199.36.153.8/30"]
    restricted-googleapis = ["199.36.153.4/30"]
    rfc1918               = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  }
}

variable "nat_external_ip_links" {
  description = "List of existing static IP self_links to use for Cloud NAT. If provided, nat_external_ips (creation) will be ignored."
  type        = list(string)
  default     = []
}

variable "nat_external_ips" {
  description = "Value for nat_external_ips."
  type        = list(object({
    name        = string
    description = string
    region      = string
  }))
  default     = []
}

variable "nat_log_filter" {
  description = "Options are ERRORS_ONLY, TRANSLATIONS_ONLY, ALL. Default value is ALL"
  type        = string
  default     = "ALL"
}

variable "nat_source_mode" {
  description = "Valid values are: ALL_SUBNETWORKS_ALL_IP_RANGES, ALL_SUBNETWORKS_ALL_PRIMARY_IP_RANGES, and LIST_OF_SUBNETWORKS. See https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/compute_router_nat#source_subnetwork_ip_ranges_to_nat"
  type        = string
  default     = "LIST_OF_SUBNETWORKS"
}

variable "network" {
  description = "Network module config with spec."
  type        = any
  default     = null
}

variable "private_google_apis" {
  description = "Allow egress to IP ranges for restricted.googleapis.com."
  type        = bool
  default     = false
}

variable "private_service_connect_cidr" {
  description = "Value for private_service_connect_cidr."
  type        = string
  default     = null
}

variable "project_iam" {
  description = "Group-centric IAM config. Expects 'spec' list with 'email' and 'roles'."
  type        = any
  default     = null
}

variable "project_iam_default" {
  description = "Default settings for group items."
  type        = object({
    email = string
    roles = list(string)
    condition = object({
      title       = string
      description = string
      expression  = string
    })
  })
  default     = {
    email     = null
    roles     = []
    condition = null
  }
}

variable "project_number" {
  description = "Project number of var.project_id. Set this to avoid permadiffs when creating tag bindings. This can be left null when reusing service accounts and tags are not used."
  type        = string
  default     = null
}

variable "restricted_google_apis" {
  description = "Allow egress to IP ranges for restricted.googleapis.com."
  type        = bool
  default     = false
}

variable "routing_mode" {
  description = "Routing mode for the VPC network."
  type        = string
  default     = "REGIONAL"
}

variable "service_account_default" {
  description = "A service account object to be merged into"
  type        = object({
    name                       = string
    display_name               = string
    description                = string
    prefix                     = string
    service_account_reuse      = bool
    tag_bindings               = map(string)
    iam_bindings               = map(list(string))
    iam_billing_roles          = map(list(string))
    iam_by_principles_additive = map(list(string))
    iam_by_principles          = map(list(string))
    iam_folder_roles           = map(list(string))
    iam_organization_roles     = map(list(string))
    iam_project_roles          = list(string)
    iam_sa_roles               = map(list(string))
    iam_storage_roles          = map(list(string))
  })
  default     = {
    name                       = null
    display_name               = "Terraform-managed"
    description                = null
    prefix                     = null
    service_account_reuse      = false
    tag_bindings               = {}
    iam_bindings               = {}
    iam_billing_roles          = {}
    iam_by_principles_additive = {}
    iam_by_principles          = {}
    iam_folder_roles           = {}
    iam_organization_roles     = {}
    iam_project_roles          = []
    iam_sa_roles               = {}
    iam_storage_roles          = {}
  }
}

variable "service_agent_iam" {
  description = "Service Agent IAM config. Expects a 'spec' list with 'service' and 'roles'."
  type        = any
  default     = null
}

variable "subnets" {
  description = "A list of subnet objects to create in the VPC. If not provided, a default 'public' and 'private' subnet will be created."
  type        = any
  default     = null
}

variable "valid_subnet_range" {
  description = "Value for valid_subnet_range."
  type        = string
  default     = "192.168.0.0/16"
}
