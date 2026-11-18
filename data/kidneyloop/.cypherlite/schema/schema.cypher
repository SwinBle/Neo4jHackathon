CREATE CONSTRAINT `antigen_name` FOR (n:`Antigen`) REQUIRE (n.`name`) IS UNIQUE;
CREATE CONSTRAINT `blood_name` FOR (n:`BloodType`) REQUIRE (n.`name`) IS UNIQUE;
CREATE CONSTRAINT `donor_id` FOR (n:`Donor`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `patient_id` FOR (n:`Patient`) REQUIRE (n.`id`) IS UNIQUE;
