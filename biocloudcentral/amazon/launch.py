"""Setup and launch Amazon web service instances.
"""
import logging

import boto
from boto.ec2.regioninfo import RegionInfo
from boto.exception import EC2ResponseError

log = logging.getLogger(__name__)

CM_POLICY =  """{
  "Statement": [
    {
      "Sid": "Stmt1319820532566",
      "Action": [
        "ec2:AttachVolume",
        "ec2:CreateSnapshot",
        "ec2:CreateTags",
        "ec2:CreateVolume",
        "ec2:DeleteSnapshot",
        "ec2:DeleteTags",
        "ec2:DeleteVolume",
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeInstanceAttribute",
        "ec2:DescribeInstances",
        "ec2:DescribeKeyPairs",
        "ec2:DescribePlacementGroups",
        "ec2:DescribeRegions",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSnapshotAttribute",
        "ec2:DescribeSnapshots",
        "ec2:DescribeTags",
        "ec2:DescribeVolumes",
        "ec2:DetachVolume",
        "ec2:GetConsoleOutput",
        "ec2:Monitoring",
        "ec2:MonitorInstances",
        "ec2:RebootInstances",
        "ec2:RunInstances",
        "ec2:TerminateInstances"
      ],
      "Effect": "Allow",
      "Resource": "*"
    },
    {
      "Sid": "Stmt1319820637269",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:DeleteObject",
        "s3:GetBucketAcl",
        "s3:GetBucketPolicy",
        "s3:GetObject",
        "s3:GetObjectAcl",
        "s3:ListAllMyBuckets",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:PutBucketAcl",
        "s3:PutBucketPolicy",
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Effect": "Allow",
      "Resource": "arn:aws:s3:::*"
    }
  ]
}"""

# ## Cloud interaction methods
def connect_ec2(a_key, s_key):
    """ Create and return an EC2 connection object.
    """
    # Use variables for forward looking flexibility
    # AWS connection values
    region_name = 'us-east-1'
    region_endpoint = 'ec2.amazonaws.com'
    is_secure = True
    ec2_port = None
    ec2_conn_path = '/'
    r = RegionInfo(name=region_name, endpoint=region_endpoint)
    ec2_conn = boto.connect_ec2(aws_access_key_id=a_key,
                          aws_secret_access_key=s_key,
                          is_secure=is_secure,
                          region=r,
                          port=ec2_port,
                          path=ec2_conn_path)
    return ec2_conn

def create_iam_user(a_key, s_key, group_name='BioCloudCentral', user_name='cloudman'):
    """ Create IAM connection, setup IAM group, add a user to it, and create a
        set of new credentials for that user.
        
        NOTE: There is a problem with this approach because, unless the user notes down
        the secret key that is created as part of the user creation, they will
        not be able to start the same cluster again (this is because a cluster
        is identified by a user account and cluster name and this method creates
        new creds each time it's invoked - see comment below).
        
        This method works only with AWS.
    """
    access_key = secret_key = None
    try:
        iam_conn = boto.connect_iam()
        # Create an IAM group that will house users, first checking if such group exists
        grps = iam_conn.get_all_groups()
        iam_grp = None
        for grp in grps.groups:
            if grp.group_name == group_name:
                iam_grp = grp
                log.debug("Found IAM group %s" % grp.group_name)
                break
        if iam_grp is None:
            log.debug("Creating IAM group %s" % group_name)
            iam_grp = iam_conn.create_group(group_name)
        # Create JSON policy and associate it with the group
        cm_policy = CM_POLICY
        cm_policy_name = '%sPolicy' % group_name
        log.debug("Adding/updating IAM group %s policy: %s" % (group_name, cm_policy_name))
        iam_conn.put_group_policy(group_name, cm_policy_name, cm_policy)
        # If not existent, create user
        usrs = iam_conn.get_all_users()
        usr = None
        for user in usrs.users:
            if user.user_name == user_name:
                usr = user
                log.debug("Found IAM user %s" % user.user_name)
                break
        if usr is None:
            log.debug("Creating IAM user %s" % user_name)
            usr = iam_conn.create_user(user_name)
        # Add user to the group (this does no harm if user is already member of the group)
        iam_conn.add_user_to_group(group_name, user_name)
        # Create access credentials for the user
        # NOTE: The secret_key is accessible only when credentials are created
        # so new credentials need to be created at each cluster instantiation
        # (because we are not storing them in a local database or anything).
        # Because the assumption can be made that this user is used only for
        # CBL & CM clusters, all other keys could be deleted after which a new
        # key could be created and used. This would not polute the user account.
        # However, if another cluster is currently on that is using existing
        # credentials, this approach would disable control of that cluster.
        # Unfortunately it is impossible to continue and create new creds because
        # only two sets of those are allowed per user...
        log.debug("Creating new access credentials for IAM user '%s'" % user_name)
        r = iam_conn.create_access_key(user_name)
        access_key = r.access_key_id
        secret_key = r.secret_access_key
    except Exception, e:
        log.error("Trouble dealing with IAM: %s" % e)
    return access_key, secret_key

def create_cm_security_group(ec2_conn, sg_name='CloudMan'):
    """ Create a security group with all authorizations required to run CloudMan.
        If the group already exists, check its rules and add the missing ones.
        Return the name of the created security group.
    """
    cmsg = None
    # Check if this security group already exists
    sgs = ec2_conn.get_all_security_groups()
    for sg in sgs:
        if sg.name == sg_name:
            cmsg = sg
            log.debug("Security group '%s' already exists; will add authorizations next." % sg_name)
            break
    # If it does not exist, create security group
    if cmsg is None:
        log.debug("Creating Security Group %s" % sg_name)
        cmsg = ec2_conn.create_security_group(sg_name, 'A security group for CloudMan')
    # Add appropriate authorization rules
    # If these rules already exist, nothing will be changed in the SG
    ports = (('80', '80'), # Web UI
             ('20', '21'), # FTP
             ('22', '22'), # ssh
             ('30000', '30100'), # FTP transfer
             ('42284', '42284')) # CloudMan UI
    for port in ports:
        try:
            if not rule_exists(cmsg.rules, from_port=port[0], to_port=port[1]):
                cmsg.authorize(ip_protocol='tcp', from_port=port[0], to_port=port[1], cidr_ip='0.0.0.0/0')
            else:
                log.debug("Rule (%s:%s) already exists in the SG" % (port[0], port[1]))
        except EC2ResponseError, e:
            log.error("A problem with security group authorizations: %s" % e)
    # Add rule that allows communication between instances in the same SG
    g_rule_exists = False # Flag to indicate if group rule already exists
    for rule in cmsg.rules:
        for grant in rule.grants:
            if grant.name == cmsg.name:
                g_rule_exists = True
                log.debug("Group rule already exists in the SG")
        if g_rule_exists:
            break
    if g_rule_exists is False: 
        try:
            cmsg.authorize(src_group=cmsg)
        except EC2ResponseError, e:
            log.error("A problem w/ security group authorization: %s" % e)
    log.info("Done configuring '%s' security group" % cmsg.name)
    return cmsg.name

def rule_exists(rules, from_port, to_port, ip_protocol='tcp', cidr_ip='0.0.0.0/0'):
    """ A convenience method to check if an authorization rule in a security
        group exists.
    """
    for rule in rules:
        if rule.ip_protocol == ip_protocol and rule.from_port == from_port and \
           rule.to_port == to_port and cidr_ip in [ip.cidr_ip for ip in rule.grants]:
            return True
    return False

def create_key_pair(ec2_conn, key_name='cloudman_key_pair'):
    """ Create a key pair with the provided name.
        Return the name of the key or None if there was an error creating the key. 
    """
    kp = None
    # Check if a key pair under the given name already exists. If it does not,
    # create it, else return.
    kps = ec2_conn.get_all_key_pairs()
    for akp in kps:
        if akp.name == key_name:
            kp = akp
            log.debug("Key pair '%s' already exists; not creating it again." % key_name)
            return kp.name
    try:
        kp = ec2_conn.create_key_pair(key_name)
    except EC2ResponseError, e:
        log.error("Problem creating key pair '%s': %s" % (key_name, e))
        return None
    # print kp.material # This should probably be displayed to the user on the screen and allow them to save the key?
    log.info("Created key pair '%s'" % kp.name)
    return kp.name

def run_instance(ec2_conn, user_provided_data, image_id='ami-ad8e4ec4', kernel_id=None, ramdisk_id=None,
                 key_name='cloudman_key_pair', security_groups=['CloudMan']):
    """ Start an instance. If instance start was OK, return the ResultSet object
        else return None.
    """
    rs = None
    instance_type = user_provided_data['instance_type']
    # Remove 'instance_type' key from the dict before creating user data
    del user_provided_data['instance_type']
    ud = "\n".join(['%s: %s' % (key, value) for key, value in user_provided_data.iteritems()])
    try:
        rs = ec2_conn.run_instances(image_id=image_id,
                                    instance_type=instance_type,
                                    key_name=key_name,
                                    security_groups=security_groups,
                                    user_data=ud,
                                    kernel_id=kernel_id,
                                    ramdisk_id=ramdisk_id)
    except EC2ResponseError, e:
        log.error("Problem starting an instance: %s" % e)
    if rs:
        try:
            log.info("Started an instance with ID %s" % rs.instances[0].id)
        except Exception, e:
            log.error("Problem with the started instance object: %s" % e)
    else:
        log.warning("Problem starting an instance?")
    return rs
