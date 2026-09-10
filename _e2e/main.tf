terraform {
  required_version = "~> 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.17.0"
    }
  }

  backend "local" {
    path = "./terraform.tfstate"
  }
}

module "gcs" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/gcs?ref=main"

  project_id                     = var.project_id
  region                         = var.region
  gcs                            = var.gcs
  bucket_default                 = var.bucket_default
}

module "network" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/network?ref=main"

  project_id                     = var.project_id
  region                         = var.region
  network                        = var.network
  routing_mode                   = var.routing_mode
  description                    = var.description
  common_resource_id             = var.common_resource_id
  custom_vpc_name                = var.custom_vpc_name
  custom_router_name             = var.custom_router_name
  custom_nat_name                = var.custom_nat_name
  custom_nat_ip_name             = var.custom_nat_ip_name
  custom_nat_ip_desc             = var.custom_nat_ip_desc
  create_nat                     = var.create_nat
  deny_egress                    = var.deny_egress
  allow_github_access            = var.allow_github_access
  allow_internal_communication   = var.allow_internal_communication
  restricted_google_apis         = var.restricted_google_apis
  private_google_apis            = var.private_google_apis
  ingress_ssh_via_IAP            = var.ingress_ssh_via_IAP
  ingress_health_check           = var.ingress_health_check
  custom_deny_egress_fw_name     = var.custom_deny_egress_fw_name
  custom_allow_internal_communication_fw_name = var.custom_allow_internal_communication_fw_name
  custom_allow_github_fw_name    = var.custom_allow_github_fw_name
  custom_allow_restricted_google_apis_fw_name = var.custom_allow_restricted_google_apis_fw_name
  custom_allow_private_google_apis_fw_name = var.custom_allow_private_google_apis_fw_name
  subnets                        = var.subnets
  nat_source_mode                = var.nat_source_mode
  nat_external_ips               = var.nat_external_ips
  nat_external_ip_links          = var.nat_external_ip_links
  valid_subnet_range             = var.valid_subnet_range
  global_address_name            = var.global_address_name
  enable_private_service_connect = var.enable_private_service_connect
  private_service_connect_cidr   = var.private_service_connect_cidr
  min_ports_per_vm               = var.min_ports_per_vm
  external_subnets_allows_nats   = var.external_subnets_allows_nats
  nat_log_filter                 = var.nat_log_filter
  create_googleapis_dns          = var.create_googleapis_dns
  googleapis_dns_mode            = var.googleapis_dns_mode
  allow_dns_egress               = var.allow_dns_egress
  allow_metadata_server_egress   = var.allow_metadata_server_egress
  export_custom_routes           = var.export_custom_routes
  import_custom_routes           = var.import_custom_routes
  export_subnet_routes_with_public_ip = var.export_subnet_routes_with_public_ip
  import_subnet_routes_with_public_ip = var.import_subnet_routes_with_public_ip
}

module "firewall" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/firewall?ref=main"

  context                        = var.context
  firewall                       = var.firewall
  default_rules_config           = var.default_rules_config
  egress_rules                   = var.egress_rules
  factories_config               = var.factories_config
  ingress_rules                  = var.ingress_rules
  named_ranges                   = var.named_ranges
  network                        = module.network.vpc_network.name
  project_id                     = var.project_id
  region                         = var.region
}

module "dns" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/dns?ref=main"

  project_id                     = var.project_id
  dns                            = var.dns
  dns_default                    = var.dns_default
}

module "external_global_address" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/external_global_address?ref=main"

  project_id                     = var.project_id
  region                         = var.region
  external_global_address        = var.external_global_address
}

module "external_global_loadbalancer" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/external_global_loadbalancer?ref=main"

  project_id                     = var.project_id
  region                         = var.region
  external_global_loadbalancer   = var.external_global_loadbalancer
}

module "iam_service_account" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/iam_service_account?ref=main"

  project_id                     = var.project_id
  project_number                 = var.project_number
  iam_service_account            = var.iam_service_account
  service_account_default        = var.service_account_default
}

module "project_iam" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/project_iam?ref=main"

  project_id                     = var.project_id
  project_iam                    = var.project_iam
  project_iam_default            = var.project_iam_default
}

module "iam_custom_role_stack" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/iam_custom_role_stack?ref=main"

  iam_custom_role_stack          = var.iam_custom_role_stack
  iam_custom_role_stack_default  = var.iam_custom_role_stack_default
}

module "service_agent_iam" {
  source = "github.com/rjones-projects/gcp_terraform-modules//terraform/modules/service_agent_iam?ref=main"

  project_id                     = var.project_id
  service_agent_iam              = var.service_agent_iam
}
