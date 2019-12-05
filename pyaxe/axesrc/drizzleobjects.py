import os
import re
import sys
import math
import numpy as np
import shutil
import logging
from copy import deepcopy
import tempfile

from astropy.io import fits
from drizzlepac import astrodrizzle
from drizzlepac import util as driz_util

from pyaxe import config as config_util
from pyaxe.axeerror import aXeError

from . import configfile
from . import drizzleobjects


# make sure there is a logger
_log = logging.getLogger(__name__)


class DrizzleParams(dict):
    """Class to store the drizzle parameters"""
    def __init__(self, confterm):
        # if necessary, split the configuration term
        conflist = confterm.strip().split(',')

        # store the name of the primary
        # configuration file
        self.config_file = conflist[0]

        # extract the drizzle parameters from the configuration file
        drizzle_params = self._load_drizzle_params(self.config_file)

        # provide some general default parameters
        drizzle_params['IN_UN'] = 'cps'
        drizzle_params['OUT_UN'] = 'cps'

        # convert the parameters into an internal
        # dictionary
        dict.__init__(self, drizzle_params)

        self['CONF'] = conflist[0]
        print(drizzle_params)

    def _set_default(self, drizzle_dict, config, keyword, default_val):
        """Provide defaults if a keyword is not given"""
        # check for the keyword in the config structure
        if config[keyword] is not None:
            drizzle_dict[keyword] = config[keyword]
        # if not given, take the default
        else:
            drizzle_dict[keyword] = default_val

    def _load_drizzle_params(self, config_file):
        """
        Extract the drizzle parameters from config file
        """

        # list with all valid kernels
        kernels = ['square',
                   'point',
                   'turbo',
                   'gaussian',
                   'tophat',
                   'lanczos3']

        # create an empty dictionary
        drizzle_params = {}

        # load the first configuration file
        config = configfile.ConfigFile(config_util.getCONF(config_file))

        # get and store the readout noise
        if config['RDNOISE'] is not None:
            drizzle_params['RDNOISE'] = float(config['RDNOISE'])
        else:
            err_msg = 'No readout noise in the configuration file!'
            raise aXeError(err_msg)

        if config['DRZRESOLA'] is not None:
            drizzle_params['RESOL'] = float(config['DRZRESOLA'])
        if config['DRZSCALE'] is not None:
            drizzle_params['SCALE'] = float(config['DRZSCALE'])
        if config['DRZLAMB0'] is not None:
            drizzle_params['LAMBDA0'] = float(config['DRZLAMB0'])

        # supply defaults in case that no values are provided
        if config['DRZROOT'] is not None:
            drizzle_params['ROOT'] = config['DRZROOT']
        else:
            drizzle_params['ROOT'] = 'aXeDrizzle'
        if config['DRZKERNEL'] is not None:
            drizzle_params['KERNEL'] = config['DRZKERNEL']
        else:
            drizzle_params['KERNEL'] = 'square'
        if config['DRZPSCALE'] is not None:
            drizzle_params['PSCALE'] = config['DRZPSCALE']
        else:
            drizzle_params['PSCALE'] = 1.0
        if config['DRZPFRAC'] is not None:
            drizzle_params['PFRAC'] = config['DRZPFRAC']
        else:
            drizzle_params['PFRAC'] = 1.0

        # check for valid drizzle kernel
        if drizzle_params['KERNEL'] not in kernels:
            err_msg = (f"The term {drizzle_params['KERNEL']} is not a valid drizzle kernel!")
            raise aXeError(err_msg)

        # return the dictionary
        return drizzle_params


class DrizzleObjectList:
    """List class for all objects to be drizzled"""
    def __init__(self,
                 drizzle_params,
                 cont_info,
                 opt_extr=False,
                 back=False,
                 drztmp_dir=None,
                 drizzle_dir=None):

        # load the drizzle parameters
        self.drizzle_params = drizzle_params.copy()

        # store the quantitative contamination flag
        self.cont_info = cont_info

        # store the optimal extraction flag
        self.opt_extr = opt_extr

        # save the drizzle tmp-directory;
        # use the default if not explicitly given
        if drztmp_dir is not None:
            self.drztmp_dir = drztmp_dir
        else:
            self.drztmp_dir = config_util.getDRZTMP()

        # save then back flag
        self.back = back

        # save the drizzle directory;
        # use the default if not explicitly given
        if drizzle_dir is not None:
            self.drizzle_dir = drizzle_dir
        else:
            self.drizzle_dir = config_util.getDRIZZLE()

        # get the identifier for drizzle objects
        self.regexp = self._get_regexp(back)

        # get all drizzle objects
        objectlist = self._find_drizzle_objects(self.drztmp_dir,
                                                self.regexp,
                                                back)

        # convert the objects list to a list of objects
        self.drizzle_objects = self._objlist_to_drzobjects(objectlist,
                                                           self.drizzle_params,
                                                           self.cont_info,
                                                           self.opt_extr,
                                                           self.back,
                                                           self.drztmp_dir,
                                                           self.drizzle_dir)

    def __str__(self):
        """Defines a string method"""
        # initialize the string
        big_string = ''

        # go over all drizzle objects
        for index in range(len(self)):
            # append its string representation
            big_string += str(self.drizzle_objects[index])

        # return the string
        return big_string

    def __len__(self):
        """Defines a length for the object"""
        return len(self.drizzle_objects)

    def __getitem__(self, index):
        """Defines an index method"""
        # return the drizzle object
        return self.drizzle_objects[index]

    def _get_regexp(self, back):
        """Stores and returns the regexp for finding drizzle objects"""

        # compile and return the regular expression
        if back:
            return re.compile("_flt_ID\\d+.BCK.fits$")
        else:
            return re.compile("_flt_ID\\d+.fits$")

    def _find_drizzle_objects(self, drztmp_dir, regexp, back):
        """Search for drizzle objects in a directory"""
        # create an empty list
        drizzle_objects = {}

        # check whether the directory exists
        if not os.path.isdir(drztmp_dir):
            # complain and out if not
            err_msg = (f"The specified path: {drztmp_dir} doens't exist or isn't a directory!")
            raise aXeError(err_msg)

        # list all content in the tmp-directory
        all_content = os.listdir(drztmp_dir)

        # go over all files
        for one_item in all_content:

            # generate the absolute path
            one_dir = os.path.join(drztmp_dir, one_item)

            # move forward if it is no directory
            if not os.path.isdir(one_dir):
                continue

            # list the content in the directory
            all_contribs = os.listdir(one_dir)

            for one_contrib in all_contribs:

                # check whether it is a flt-extension;
                # continue if not
                found = regexp.search(one_contrib)
                if found is None:
                    continue

                # find the root name and the ID number of the file
                ID, file_root = self._identify_drizzle_file(found,
                                                            one_contrib,
                                                            back)

                # either append the file to an existing
                # dictionary entry or start a new one
                if ID in drizzle_objects:
                    drizzle_objects[ID].append(os.path.join(one_item,
                                                            file_root))
                else:
                    drizzle_objects[ID] = [os.path.join(one_item, file_root)]

        # return the entire dictionary
        return drizzle_objects

    def _identify_drizzle_file(self, found, one_file, back):
        """Get the ID and the root name of a drizzle file"""
        # get the part matching the expression
        fspan = found.span()

        # identify the root name
        file_root = one_file[:fspan[0]]

        # identify the extension,
        # get the ID number within the extension
        ext = one_file[fspan[0]:fspan[1]]
        pos1 = ext.find("ID")
        if back:
            pos2 = ext.find(".BCK.fits")
        else:
            pos2 = ext.find(".fits")
        ID = ext[pos1:pos2]

        # return ID and root name
        return ID, file_root

    def _objlist_to_drzobjects(self, objectlist, drizzle_params, cont_info,
                               opt_extr, back, drztmp_dir, drizzle_dir):
        """Converts the object list into drizzle objects"""
        # create an empty list
        drzobjects = []

        # split the dictionary
        # into a key-value list
        item_list = objectlist.items()

        # go over the list
        for an_item in item_list:
            # create a drizzle object and append it to the list
            drzobjects.append(DrizzleObject(an_item[0], an_item[1],
                                            drizzle_params, cont_info,
                                            opt_extr, back, drztmp_dir,
                                            drizzle_dir))

        # return the list
        return drzobjects

    def _regroup(self):
        """Move the images to new locations"""
        # go over all drizzle objects
        for drizzleObject in self.drizzle_objects:

            # regroup the files for one object
            drizzleObject.regroup()

        # list the whole tmp-directory
        for one_location in os.listdir(self.drztmp_dir):

            # compose the absolute path
            abs_path = os.path.join(self.drztmp_dir, one_location)

            # move on for files
            if not os.path.isdir(abs_path):
                continue

            # remove empty directories
            if len(os.listdir(abs_path)) < 1:
                os.rmdir(abs_path)

    def make_OAF_file(self, infwhm, outfwhm, af_file):
        """Generate the OAF file for the drizzled images"""
        # delete previous versions of the OAF
        if os.path.isfile(af_file):
            os.unlink(af_file)

        # generate a new OAF
        oaf = open(af_file, 'w+')

        # go over all drizzle objects
        for drizzleObject in self.drizzle_objects:
            # write the entry for the object to the OAF
            oaf.write(drizzleObject.make_oaf_entry(infwhm, outfwhm))

        # close the OAF
        oaf.close()

    def get_mef_files(self):
        """Return list of MEF files"""
        # make an empty list
        mef_files = []

        # go over the drizzle objects
        for drizzleObject in self.drizzle_objects:
            # append the current MEF file name
            mef_files.append(os.path.basename(drizzleObject.ext_names['MEF']))

        # return the list
        return mef_files

    def sort(self):
        """Sort the list of drizzle objects"""
        # sort the list of drizzle objects
        self.drizzle_objects.sort()

    # def sort_drz_contrib(self, inima):
    #     """Sort the contributors of the drizzle objects"""
    #     # generate an empty list
    #     sortList = []

    #     # open the input image list
    #     if os.access(inima, os.F_OK):
    #         inlist = open(inima, 'r')

    #     # go over the list
    #     for index in range(inlist.nrows):
    #         # extract the name of the grism image
    #         grism = os.path.basename(inlist[0][index].strip())

    #         # check for the fits-extension
    #         rpos = grism.rfind(".fits")

    #         # append the root to the list
    #         if rpos > -1:
    #             sortList.append(grism[:rpos])

    #     # go over all drizzle objects
    #     for dObject in self.drizzle_objects:

    #         # generate the sort index
    #         dObject.make_sortIndex(sortList)

    #         # sort the drizzle object
    #         dObject.sort()

    def check_files(self):
        """Check the files in the the whole list"""
        # move the files
        self._regroup()

        # create list for
        # empty objects
        del_indices = []

        # go over all drizzle object
        for index in range(len(self.drizzle_objects)):
            # check the files for one object
            self.drizzle_objects[index].check_files()

            # mark the object with ZERO memebers
            if len(self.drizzle_objects[index]) < 1:
                del_indices.append(index)

        # inverse sort the indices
        # delete object with ZERO members
        del_indices.sort(reverse=True)
        for one_index in del_indices:
            _log.info(f"Deleting empty object: {str(self.drizzle_objects[one_index].objID)}!")
            del self.drizzle_objects[one_index]

    def delete_files(self):
        """Delete all files"""
        for drizzleObject in self.drizzle_objects:
            drizzleObject.delete_files()

    def prepare_drizzle(self):
        """Prepare the drizzling"""
        for drizzleObject in self.drizzle_objects:
            # prepare drizzle in one object
            drizzleObject.prepare_drizzle()

    def drizzle(self):
        """Drizzle all objects"""
        for drizzleObject in self.drizzle_objects:
            # prepare drizzle in one object
            drizzleObject.drizzle()

            # combine the layers to a MEF file
            drizzleObject.make_mef()


class DrizzleObject:
    """List class for all objects to be drizzled"""
    def __init__(self, objID, file_list, drizzle_params, cont_info,
                 opt_extr, back, drztmp_dir, drizzle_dir):

        self.ID = int(objID[2:])

        # define and save the object ID
        self.objID = objID

        # save the drizzle parameters
        self.drizzle_params = drizzle_params.copy()

        # save the quantitative contamination flag
        self.cont_info = cont_info

        # save the optimal extraction flag
        self.opt_extr = opt_extr

        # save the back flag
        self.back = back

        # save the drizzle directory
        # and the drizzle tmp-directory
        self.drztmp_dir = drztmp_dir
        self.drizzle_dir = drizzle_dir

        # define the name of the object directory
        self.objID_dir = self._get_objID_dirname(self.objID,
                                                 self.drztmp_dir,
                                                 self.back)

        # generate the list of contributors
        self.contrib_list = self._make_contrib_list(self.objID,
                                                    file_list,
                                                    opt_extr,
                                                    back,
                                                    drztmp_dir)

        # determine all relevant names
        self.ext_names = self._get_ext_names(self.drizzle_params['ROOT'],
                                             self.objID, back, drizzle_dir)

        # get the number of contributors
        self.ncontrib = self._get_ncontrib()

        self.npix = None
        self.nwht = None

    def __str__(self):
        """Defines a string representation"""
        return (f"{self.objID}: {len(self)} image contributions.\n")

    def __len__(self):
        """Defines a length"""
        return len(self.contrib_list)

    def __lt__(self, compObject):
        """Define a comparison for the object"""
        # make the comparison according
        # to the member 'sortIndex'
        if self.objID < compObject.objID:
            return -1
        elif self.objID == compObject.objID:
            return 0
        else:
            return 1

    def _get_objID_dirname(self, objID, drztmp_dir, back):
        """Define the name of the object directory"""
        # compose th name
        if back:
            objID_dir = os.path.join(drztmp_dir, f'{objID}.BCK')
        else:
            objID_dir = os.path.join(drztmp_dir, f'{objID}.OBJ')

        # return the name
        return objID_dir

    def _get_ncontrib(self):
        """Determine the number of contributors"""
        # return the number of contributing images
        return len(self.contrib_list)

    def _get_ext_names(self, file_root, objID, back, drizzle_dir):
        """Determine all necessary filenames for drizzle output"""
        # create an empty dictionary
        ext_names = {}

        if back:
            # output names of file which are part of the final drizzle result
            ext_names['FLT'] = os.path.join(drizzle_dir,
                                            f'{file_root}_flt_{objID}.BCK.fits')
            ext_names['ERR'] = os.path.join(drizzle_dir,
                                            f'{file_root}_err_{objID}.BCK.fits')
            ext_names['CON'] = os.path.join(drizzle_dir,
                                            f'{file_root}_con_{objID}.BCK.fits')
            ext_names['WHT'] = os.path.join(drizzle_dir,
                                            f'{file_root}_wht_{objID}.BCK.fits')
            ext_names['MOD'] = os.path.join(drizzle_dir,
                                            f'{file_root}_mod_{objID}.BCK.fits')
            ext_names['VAR'] = os.path.join(drizzle_dir,
                                            f'{file_root}_var_{objID}.BCK.fits')

            # names of various obsolete weight files created during
            # the drizzling of different layers
            ext_names['ERRWHT'] = os.path.join(drizzle_dir,
                                               f'{file_root}_errwht_{objID}.BCK.fits')
            ext_names['CONWHT'] = os.path.join(drizzle_dir,
                                               f'{file_root}_conwht_{objID}.BCK.fits')

            # name of the final, multi-extension fits file
            ext_names['MEF'] = os.path.join(drizzle_dir,
                                            f'{file_root}_mef_{objID}.BCK.fits')
        else:

            # output name of the median combined image
            ext_names['MED'] = os.path.join(drizzle_dir,
                                            '{0:s}_med_{1:s}.fits'
                                            .format(file_root, objID))

            # output names of file which are part of the final drizzle result
            ext_names['FLT'] = os.path.join(drizzle_dir,
                                            '{0:s}_flt_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['ERR'] = os.path.join(drizzle_dir,
                                            '{0:s}_err_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['CON'] = os.path.join(drizzle_dir,
                                            '{0:s}_con_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['WHT'] = os.path.join(drizzle_dir,
                                            '{0:s}_wht_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['MOD'] = os.path.join(drizzle_dir,
                                            '{0:s}_mod_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['VAR'] = os.path.join(drizzle_dir,
                                            '{0:s}_var_{1:s}.fits'
                                            .format(file_root, objID))

            # names of various obsolete weight files created during
            # the drizzling of different layers
            ext_names['ERRWHT'] = os.path.join(drizzle_dir,
                                               '{0:s}_errwht_{1:s}.fits'
                                               .format(file_root, objID))
            ext_names['CONWHT'] = os.path.join(drizzle_dir,
                                               '{0:s}_conwht_{1:s}.fits'
                                               .format(file_root, objID))

            # name of the final, multi-extension fits file
            ext_names['MEF'] = os.path.join(drizzle_dir,
                                            '{0:s}_mef_{1:s}.fits'
                                            .format(file_root, objID))

        # return the dictionary
        return ext_names

    def _make_contrib_list(self, objID, file_list, opt_extr, back, drztmp_dir):
        """Generates a list of contributing objects"""
        # make an empty list
        contrib_list = []

        # go over all contributing files
        for a_file in file_list:
            # generate an object and append it to the list
            contrib_list.append(DrizzleObjectContrib(a_file, objID, opt_extr,
                                                     back, drztmp_dir))

        # return the llist of contributors
        return contrib_list

    def _set_drizzle_dimensions(self):
        """Determine the dimensional parameters for the drizzle"""
        # initialize a dictionary
        drzimg_info = {}

        # create empty arrays
        length = []
        owidth = []
        xoffs = []
        drzwidth = []
        slitwidt = []

        # go over all contributing objects
        for one_contrib in self.contrib_list:
            # transfer the information
            length.append(one_contrib.info['LENGTH'])
            owidth.append(one_contrib.info['OWIDTH'])
            xoffs.append(one_contrib.info['XOFFS'])
            drzwidth.append(one_contrib.info['DRZWIDTH'])

            # check whether the slitwidth exists
            if 'SLITWIDT' in one_contrib.info:
                # store the slitwidth in an array
                slitwidt.append(one_contrib.info['SLITWIDT'])

        # convert to numpy arrays
        length_arr = np.array(length)
        owidth_arr = np.array(owidth)
        xoffs_arr = np.array(xoffs)
        drzwidth_arr = np.array(drzwidth)

        # if possible, compute the mean slitwidth
        if len(slitwidt) > 0:
            slitwidt_mean = np.array(slitwidt).mean()
        # set to None
        else:
            slitwidt_mean = None

        # convert and store the dimension of the drizzled images
        self.drizzle_params['OUTNX'] = int(length_arr.mean())
        self.drizzle_params['OUTNY'] = (2 *
                                        int(math.ceil(owidth_arr.mean())) + 10)

        # copy the image dimension to the dictionary
        drzimg_info['OUTNX'] = self.drizzle_params['OUTNX']
        drzimg_info['OUTNY'] = self.drizzle_params['OUTNY']
        drzimg_info['REFPNTX'] = xoffs_arr.mean()
        drzimg_info['REFPNTY'] = drzimg_info['OUTNY']/2+1.0
        drzimg_info['DRZWIDTH'] = drzwidth_arr.mean()
        drzimg_info['SLITWIDT'] = slitwidt_mean

        # return the dictionary
        return drzimg_info

    def _convert_variance(self):
        """Adjust the variance image"""

        # Invert the variance image
        file_a = fits.open(self.ext_names['VAR'], 'update')
        ind0 = file_a[0].data < 1.0e-16
        ind1 = file_a[0].data >= 1.0e-16
        file_a[0].data[ind0] = 0.0
        file_a[0].data[ind1] = 1.0 / file_a[0].data[ind1]
        file_a.close()

    def _convert_error(self):
        """Treat the drizzled error image"""

        # The next lines check whether the weight image
        # has negative values. If yes, it is multiplied
        # by "-1.0". This is a fix to the drizzle-decennium
        # and will, artr some point, become obsolete

        # open the image
        fits_img = fits.open(self.ext_names['WHT'], mode='readonly')
        fits_data = fits_img[0].data

        # compute some statistics
        img_ave = np.mean(fits_data)

        # close the image
        fits_img.close()

        # decide whether something must be done
        if img_ave < 0.0:

            # open the image
            fits_img = fits.open(self.ext_names['WHT'], 'update')

            # invert the data
            fits_img[0].data = -1.0 * fits_img[0].data

            # close the image
            fits_img.close()

        # Compute sqrt(ERR)/WHT for exposure time weighting
        file_a = fits.open(self.ext_names['ERR'], 'update')
        file_b = fits.open(self.ext_names['WHT'])
        ind0 = file_b[0].data < 1.0e-16
        ind1 = file_b[0].data >= 1.0e-16
        file_a[0].data[ind0] = 0.0
        file_a[0].data[ind1] = ((file_a[0].data[ind1]**0.5) /
                                file_b[0].data[ind1])
        file_a.close()
        file_b.close()

    def _correct_contam(self):
        """Correct the contamination image (for geometric contamination)"""
        # Replace contamination values with nearest integer
        file_a = fits.open(self.ext_names['CON'], 'update')
        file_a[0].data = np.rint(file_a[0].data)
        file_a.close()

    def _fill_header(self, header):
        """Write some header keywords"""
        # fill header with some keywords;
        # put 'UNKNOWN' if the contamination mode is not known
        header['NUM_DRIZ'] = (self.ncontrib, 'NUMBER OF IMAGES DRIZZLED')
        if self.cont_info is not None:
            header['CONTAM'] = (self.cont_info[0].strip(),
                                'contamination model')
        else:
            header['CONTAM'] = ("UNKNOWN", "contamination model")

        # go over all contributors
        index = 0
        for one_contrib in self.contrib_list:
            # make the keyword
            kword = 'IMG%04i'.format(index + 1)

            # make the comment
            comment = 'contributing image #%i'.format(index + 1)

            # store the image name
            header[kword] = (one_contrib.rootname, comment)

            # enhance the index
            index += 1

    def _make_WCS_header(self):
        """Generate the WCS header"""

        # make a dict for the WCS keys
        WCS_input = {}

        # open one single layer image
        fits_input = fits.open(self.ext_names['FLT'], mode='readonly')

        # extract the keywords from he header
        WCS_input['CDSCALE'] = fits_input[0].header['CDSCALE']
        WCS_input['REFPNTY'] = fits_input[0].header['REFPNTY']
        WCS_input['DLAMBDA'] = fits_input[0].header['DLAMBDA']
        WCS_input['LAMBDA0'] = fits_input[0].header['LAMBDA0']
        WCS_input['XOFFS'] = fits_input[0].header['XOFFS']

        # use also internal data
        WCS_input['YOFFS'] = self.drzimg_info['OUTNY'] / 2 + 1.0

        # close the image
        fits_input.close()

        # open the contributing object image
        fits_output = fits.open(self.ext_names['MEF'], 'update')

        # go over all data layers
        for index in range(1, len(fits_output)):
            # insert the new items in inverse order all after 'DATe'
            # this way they will appear in correct order at the beginning
            fits_output[index].header['CDELT2'] = (WCS_input['CDSCALE'],
                                                   "[arcsec/pixel] cross-dispersion scale")
            fits_output[index].header['CRVAL2'] = (0.0,
                                                   '[arcsec] reference value')
            fits_output[index].header['CRPIX2'] = (WCS_input['YOFFS'],
                                                   '[pix] reference pixel')
            fits_output[index].header['CUNIT2'] = ('arcsec',
                                                   'cross-dispersion units')
            fits_output[index].header['CTYPE2'] = ('CRDIST',
                                                   'cross-dispersion distance')
            fits_output[index].header['CDELT1'] = (WCS_input['DLAMBDA'],
                                                   '[Angstrom/pixel] dispersion')
            fits_output[index].header['CRVAL1'] = (WCS_input['LAMBDA0'],
                                                   '[Angstrom] reference value')
            fits_output[index].header['CRPIX1'] = (WCS_input['XOFFS'],
                                                   '[pixel] reference pixel')
            fits_output[index].header['CUNIT1'] = ('Angstrom',
                                                   'dispersion units')
            fits_output[index].header['CTYPE1'] = ('WAVE',
                                                   'grating dispersion function')

        # save the modified image/header;
        # and close the fits
        fits_output.flush()
        fits_output.close()

    def _compose_mef_image(self):
        """Compose the multi-extension fit image from the drizzled layers"""
        # create a fits list;
        # create aprimary header;
        # put header to fits list
        mex_hdu = fits.HDUList()
        hdrpr = fits.PrimaryHDU()
        mex_hdu.append(hdrpr)

        # fill header with some keywords
        self._fill_header(mex_hdu[0].header)

        # save and close wfits
        mex_hdu.writeto(self.ext_names['MEF'])
        mex_hdu.close()

        # Copy FLT image to MEF SCI extension
        file = fits.open(self.ext_names['FLT'], mode='readonly')
        file[0].header['extname']=('SCI')
        file[0].header['extver']= 1
        fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
        file.close()

        # Copy ERR image to MEF ERR extension
        file = fits.open(self.ext_names['ERR'], mode='readonly')
        file[0].header['extname'] = 'ERR'
        file[0].header['extver'] = 1
        fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
        file.close()

        # Copy WHT image to MEF EXPT extension
        file = fits.open(self.ext_names['WHT'], mode='readonly')
        file[0].header['extname'] = 'EXPT'
        file[0].header['extver'] = 1
        fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
        file.close()

        # Copy CON image to MEF CON extension
        file = fits.open(self.ext_names['CON'], mode='readonly')
        file[0].header['extname'] = 'CON'
        file[0].header['extver'] = 1
        fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
        file.close()

        if self.opt_extr:
            # Copy MOD image to MEF MOD extension
            file = fits.open(self.ext_names['MOD'], mode='readonly')
            file[0].header['extname'] = 'MOD'
            file[0].header['extver'] = 1
            fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
            file.close()

            # Copy VAR image to MEF VAR extension
            file = fits.open(self.ext_names['VAR'], mode='readonly')
            file[0].header['extname'] = 'VAR'
            file[0].header['extver'] = 1
            fits.append(self.ext_names['MEF'], file[0].data, file[0].header)
            file.close()

        # make the WCS header
        self._make_WCS_header()

        # delete the single images
        os.unlink(self.ext_names['FLT'])
        os.unlink(self.ext_names['ERR'])
        os.unlink(self.ext_names['WHT'])
        os.unlink(self.ext_names['CON'])

        # delete also some temporary images
        if os.path.isfile(self.ext_names['ERRWHT']):
            os.unlink(self.ext_names['ERRWHT'])
        if os.path.isfile(self.ext_names['CONWHT']):
            os.unlink(self.ext_names['CONWHT'])

        if self.opt_extr:
            os.unlink(self.ext_names['MOD'])
            os.unlink(self.ext_names['VAR'])

    def make_sortIndex(self, sortList):
        """Generate the sort index"""
        # go over all contributors
        for oneContrib in self.contrib_list:
            # generate the sort index
            oneContrib.make_sortIndex(sortList)

    def sort(self):
        """Sort the contributors"""
        # sort the list of contributors
        self.contrib_list.sort()

    def check_files(self):
        """Check for all files"""
        # first delete all remnants of
        # previous runs

        # go over all drizzle filenames
        for one_file in self.ext_names.values():
            # if the file exists
            if os.path.isfile(one_file):
                os.unlink(one_file)
                print(f'Deleted previous file: {one_file}!')

        # iterate backwards over
        # the contributors
        deleted = 0
        r_index = len(self)
        for index in range(len(self)):
            # adjust the index
            r_index -= 1

            # check whether the contributor is empty
            if self.contrib_list[r_index].isempty():
                # delete all files
                self.contrib_list[r_index].delete_files()

                # delete from contributor list
                del self.contrib_list[r_index]

                # enhance counter
                deleted += 1

            else:
                # make sure all files exist
                self.contrib_list[r_index].check_files()

        # feedback on deleted objects
        if deleted > 0:
            print(f"Object {self.objID}: {deleted} empty contributors deleted.")

        # re-define the number of contributors
        self.ncontrib = self._get_ncontrib()

    def delete_files(self, keep_mef=True):
        """Delete all files"""
        # make an empty list
        delete_keys = []

        # go over all extensions
        for one_key in self.ext_names.keys():

            # if desired, keep the MEF-extension
            if keep_mef and one_key == 'MEF':
                continue

            # add the key to the list
            delete_keys.append(one_key)

        # go over all extensions
        for one_key in delete_keys:

            # if the file exists, delete it
            if os.path.isfile(self.ext_names[one_key]):
                os.unlink(self.ext_names[one_key])

        # go over all contributing objects
        for one_contrib in self.contrib_list:
            # check the files there
            one_contrib.delete_files()

        # delete the object directory
        if os.path.isdir(self.objID_dir):
            os.rmdir(self.objID_dir)

    def regroup(self):
        """Move the contributing files to the object directory.

        Parameters
        ----------
        None

        """
        # create the object directory,
        if not os.path.isdir(self.objID_dir):
            os.mkdir(self.objID_dir)

        # go over all contributing objects
        for one_contrib in self.contrib_list:
            # check the files there
            one_contrib.regroup(self.objID_dir)

    def prepare_drizzle(self):
        """Prepare the drizzling"""
        # go over all contributing objects
        for one_contrib in self.contrib_list:
            # check the files there
            one_contrib.prepare_drizzle()

        # set the dimensions for
        self.drzimg_info = self._set_drizzle_dimensions()

        # go over all contributing objects
        self.wht_info = []
        for one_contrib in self.contrib_list:
            one_contrib.get_wht_info()

    def get_reject_info(self):
        """Get information on the weights"""
        # make an empty dict
        reject_info = {}

        # go over all contributing objects
        for one_contrib in self.contrib_list:

            # store the number of good pixel
            # before rejection
            ngood_old = deepcopy(one_contrib.nwht)

            # get the number of good pixels
            # now (presumably after rejection)
            one_contrib.get_wht_info()

            # check whether there exists statistics
            if (ngood_old and self.npix and self.nwht):
                # compute the number of rejected pixels
                nreject = ngood_old - self.nwht

                # compute the fraction of rejected pixels
                frac_reject = float(ngood_old - self.nwht) / float(ngood_old)

                # put the information to the dictionary
                reject_info[one_contrib.rootname] = [nreject, frac_reject]

        # return the information
        return reject_info

    def update_reject_info(self, reject_info):
        """Stores keywords with info's on the rejection process"""
        # open the MEF imag
        mef_image = fits.open(self.ext_names['MEF'], 'update')

        # get the header
        header = mef_image[0].header

        # check for previous info
        if 'NUM_DRIZ' in header:

            # get the number
            num_driz = header['NUM_DRIZ']

            # go over all images
            for index in range(num_driz):

                # form the keyword
                img_kword = "IMG{0:04d".format(index + 1)

                # check whether the image name is reported and
                # whether there is data on the rejection
                if img_kword in header and header[img_kword] in reject_info:

                    # store the number of pixels
                    kword1 = "NRE{0:04d}".format(index + 1)
                    kval1 = reject_info[header[img_kword]][0]
                    comment1 = ("number of rejected pixels image #{0:d}"
                                .format(index + 1))
                    header[kword1] = (kval1, comment1)

                    # store the fraction data
                    kword2 = "RFR{0:04d".format(index + 1)
                    kval2 = ("{0:0.2f}"
                             .format(100.0*reject_info[header[img_kword]][1]))
                    comment2 = ("[%] fraction of rejected pixels image #{0:d}"
                                .format(index + 1))
                    header[kword2] = (float(kval2), comment2)

        else:
            header['NUM_DRIZ'] = (len(reject_info),
                                  'NUMBER OF IMAGES DRIZZLED')
            all_keys = reject_info.keys()
            index = 0
            for one_key in all_keys:
                # form the keyword
                kword1 = "IMG{0:04d}".format(index + 1)

                # make the comment
                comment1 = "contributing image #{0:d}".format(index + 1)

                # store the image name
                header[kword1] = (one_key, comment1)

                # store the pixel data
                kword2 = "NRE{0:04d}".format(index + 1)
                kval2 = reject_info[one_key][0]
                comment2 = ("number of rejected pixels image #{0:d}"
                            .format(index + 1))
                header[kword2] = (kval2, comment2)

                # store the fraction data
                kword3 = "RFR{0:04d}".format(index + 1)
                kval3 = "{0:0.2f}".format(100.0*reject_info[one_key][1])
                comment3 = ("[%] fraction of rejected pixels image #{0:d}"
                            .format(index + 1))
                header[kword3] = (float(kval3), comment3)

                # enhance the counter
                index += 1

        # save and close file
        mef_image.flush()
        mef_image.close()

    def _create_small_fits_ctx(self, x, y):
        """Create a small fits image for use as a context for astrodrizzle"""
        data = np.ones((y,x))
        hdu = fits.PrimaryHDU(data)
        handle, filename = tempfile.mkstemp(suffix='.fits')
        hdu.writeto(filename)
        os.close(handle)
        del hdu
        del data
        return (filename)

    def drizzle(self):
        """Drizzle all contributors together"""
        if self.back:
            msg = ("Drizzling background object: {0:10s} ... "
                   .format(self.objID))
        else:
            msg = f"Drizzling object : {self.objID} ... "
        print(msg)
        sys.stdout.flush()

        # create a drizzle object
        # drizzleObject = astrodrizzle.adrizzle.drizzle()

        # go over all contributing objects
        for one_contrib in self.contrib_list:

            img_nx = int(one_contrib.info['LENGTH'])
            img_ny = 2*int(math.ceil(one_contrib.info['OWIDTH'])) + 10

            # run drizzle for the object data
            print(f"drizzle input filename is: {one_contrib.ext_names['FLT']}")

            # drizzlepac is requiring a context image array to run
            # I'm told this is a requirement in the C drizzle code right now
            # and was advised to try a small empty array

            ctx_name = self._create_small_fits_ctx(img_nx, img_ny)
            # create a default configObj
            configObj = driz_util.getDefaultConfigObj("adrizzle", 'defaults')
            configObj["input"] = one_contrib.ext_names['FLT']
            configObj["inweight"] = one_contrib.ext_names['WHT']
            configObj["outdata"] = self.ext_names['FLT']
            configObj["outweight"] = self.ext_names['WHT']
            configObj['outcontext'] = ctx_name
            configObj['wt_scl'] = 'exptime'
            configObj['coeffs'] = one_contrib.ext_names['CFF']
            configObj['pixfrac'] = self.drizzle_params['PFRAC']
            configObj['kernel'] = self.drizzle_params['KERNEL']
            configObj['Data Scaling Parameters'] = {'expkey':"EXPTIME",
                                                    'in_units':self.drizzle_params['IN_UN'],
                                                    'out_units':self.drizzle_params['OUT_UN'],
                                                    'fillval': None}
            configObj['User WCS Parameters'] = {'refimage':"",'outscale':"",'outnx': img_nx, 'outny': img_ny}
            astrodrizzle.adrizzle.run(configObj)
            os.remove(ctx_name)

            ctx_name = self._create_small_fits_ctx(img_nx, img_ny)
            # run drizzle for the contamination data
            configObj["input"] = one_contrib.ext_names['CON']
            configObj["inweight"] = one_contrib.ext_names['WHT']
            configObj["outdata"] = self.ext_names['CON']
            configObj["outweight"] = self.ext_names['CONWHT']
            configObj['outcontext'] = ctx_name
            configObj['wt_scl'] = 'exptime'
            configObj['coeffs'] = one_contrib.ext_names['CFF']
            configObj['pixfrac'] = self.drizzle_params['PFRAC']
            configObj['kernel'] = self.drizzle_params['KERNEL']
            configObj['Data Scaling Parameters'] = {'expkey':"EXPTIME",
                                                    'in_units':self.drizzle_params['IN_UN'],
                                                    'out_units':self.drizzle_params['OUT_UN'],
                                                    'fillval': None}
            configObj['User WCS Parameters'] = {'refimage':"",'outscale':"",'outnx': img_nx, 'outny': img_ny}
            astrodrizzle.adrizzle.run(configObj)
            os.remove(ctx_name)

            # run drizzle for the error data
            # the format for in- and output must be changed
            self.drizzle_params['IN_UN'] = 'counts'
            self.drizzle_params['OUT_UN'] = 'counts'

            # run drizzle for the contamination data
            configObj["input"] = one_contrib.ext_names['ERR']
            configObj["inweight"] = one_contrib.ext_names['WHT']
            configObj["outdata"] = self.ext_names['ERR']
            configObj["outweight"] = self.ext_names['ERRWHT']
            
            astrodrizzle.adrizzle.run(configObj)

            self.drizzle_params['IN_UN'] = 'cps'
            self.drizzle_params['OUT_UN'] = 'cps'

            # in case of optimal extraction,
            # drizzle the model image....
            if self.opt_extr:
                configObj["input"] = one_contrib.ext_names['MOD']
                configObj["inweight"] = one_contrib.ext_names['VAR']
                configObj["outdata"] = self.ext_names['MOD']
                configObj["outweight"] = self.ext_names['VAR']
                configObj['coeffs'] = one_contrib.ext_names['CFF']
                astrodrizzle.adrizzle.run(configObj)

        # give feedback
        print('Done!')

    def make_mef(self):
        """Generate a MultiExtension FITS image."""

        # check for geometric contamination
        if ((self.cont_info is not None) and (not self.cont_info[1])):
            # correct the contamination image
            self._correct_contam()

        # convert the error image
        self._convert_error()

        if self.opt_extr:
            self._convert_variance()

        # compose the multi extension fits image
        self._compose_mef_image()

        # return the MEF name
        return os.path.basename(self.ext_names['MEF'])

    def make_oaf_entry(self, infwhm, outfwhm):
        """Generate an OAF entry"""
        # define a string
        big_string = ''
        big_string += f'APERTURE {self.ID}\n'
        big_string += f'  BEAM A\n'
        big_string += f'     REFPIXEL{self.ID}A {self.drzimg_info["REFPNTX"]-1.0} {self.drzimg_info["REFPNTY"]-1.0}\n'
        big_string += f'     CORNERS{self.ID}A 1.0 1.0 {self.drzimg_info["OUTNX"]} 1.0 {self.drzimg_info["OUTNX"]} {self.drzimg_info["OUTNY"]} 1.0 {self.drzimg_info["OUTNY"]}\n'
        big_string += f'     CURVE{self.ID}A   1 0.0 0.0\n'
        big_string += f'     WIDTH{self.ID}A   {self.drzimg_info["DRZWIDTH"]/infwhm*outfwhm}\n'
        big_string += f'     ORIENT{self.ID}A  90.0\n'
        if self.drzimg_info['SLITWIDT']:
            big_string += f'     SLITGEOM{self.ID}A 0.0 0.0 {self.drzimg_info["SLITWIDT"]} 0.0\n'
        big_string += f'     IGNORE{self.ID}A   0\n'
        big_string += f'  BEAM END\n'
        big_string += f'APERTURE END\n'

        # return the big string
        return big_string


class DrizzleObjectContrib:
    """Class for a contributing image to a drizzle object."""

    def __init__(self, file_root, objID, opt_extr, back, drztmp_dir):
        self.rootname = self._get_rootname(file_root)
        self.objID = objID
        self.opt_extr = opt_extr
        self.drztmp_dir = drztmp_dir

        # determine the names of all possible input
        # files for the drizzle process
        self.ext_names = self._get_ext_names(file_root,
                                             objID,
                                             back,
                                             drztmp_dir)

        # initialize the sort index
        self.sortIndex = 0

    def __lt__(self, compObject):
        """Define a comparison for the object."""

        # make the comparison according
        # to the member 'sortIndex'
        if self.sortIndex < compObject.sortIndex:
            return -1
        else:
            return self.sortIndex == compObject.sortIndex

    def _get_rootname(self, file_root_path):
        """Find the root name for a file"""
        file_root = os.path.basename(file_root_path)

        # check for underscores
        if file_root.find('_') > 0:
            # set the name to the first section as root name
            rootname = file_root.split('_')[0]
        else:
            # set everything as root name
            rootname = file_root

        # return the root name
        return rootname

    def _get_ext_names(self, file_root, objID, back, drztmp_dir):
        """Determine all possible filenames for drizzle input"""
        ext_names = {}

        if back:
            # fill the dictionary will all possible input files for the
            # aXedrizzle process
            ext_names['FLT'] = os.path.join(drztmp_dir,
                                            '{0:s}_flt_{1:s}.BCK.fits'
                                            .format(file_root, objID))
            ext_names['ERR'] = os.path.join(drztmp_dir,
                                            '{0:s}_err_{1:s}.BCK.fits'
                                            .format(file_root, objID))
            ext_names['CON'] = os.path.join(drztmp_dir,
                                            '{0:s}_con_{1:s}.BCK.fits'
                                            .format(file_root, objID))
            ext_names['WHT'] = os.path.join(drztmp_dir,
                                            '{0:s}_wht_{1:s}.BCK.fits'
                                            .format(file_root, objID))
            ext_names['MOD'] = os.path.join(drztmp_dir,
                                            '{0:s}_mod_{1:s}.BCK.fits'
                                            .format(file_root, objID))
            ext_names['VAR'] = os.path.join(drztmp_dir,
                                            '{0:s}_var_{1:s}.BCK.fits'
                                            .format(file_root, objID))

            # name of the drizzle coefficients file
            ext_names['CFF'] = os.path.join(drztmp_dir,
                                            '{0:s}_flt_{1:s}.coeffs'
                                            .format(file_root, objID))

        else:
            # fill the dictionary will all possible input files
            # for the aXedrizzle process
            ext_names['FLT'] = os.path.join(drztmp_dir,
                                            '{0:s}_flt_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['ERR'] = os.path.join(drztmp_dir,
                                            '{0:s}_err_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['CON'] = os.path.join(drztmp_dir,
                                            '{0:s}_con_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['WHT'] = os.path.join(drztmp_dir,
                                            '{0:s}_wht_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['MOD'] = os.path.join(drztmp_dir,
                                            '{0:s}_mod_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['VAR'] = os.path.join(drztmp_dir,
                                            '{0:s}_var_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['BLT'] = os.path.join(drztmp_dir,
                                            '{0:s}_blt_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['DER'] = os.path.join(drztmp_dir,
                                            '{0:s}_der_{1:s}.fits'
                                            .format(file_root, objID))
            ext_names['CRR'] = os.path.join(drztmp_dir,
                                            '{0:s}_crr_{1:s}.fits'
                                            .format(file_root, objID))

            ext_names['SING_SCI'] = os.path.join(drztmp_dir,
                                                 '{0:s}_single_sci_{1:s}.fits'
                                                 .format(file_root, objID))
            ext_names['SING_WHT'] = os.path.join(drztmp_dir,
                                                 '{0:s}_single_wht_{1:s}.fits'
                                                 .format(file_root, objID))

            # name of the drizzle coefficients file
            ext_names['CFF'] = os.path.join(drztmp_dir,
                                            '{0:s}_flt_{0:s}.coeffs'
                                            .format(file_root, objID))

        # return the dictionary
        return ext_names

    def _get_header_info(self):
        """Set the exposure time from the object contributor."""

        # for self-information
        self.info = {}

        # the list of mandatory keywords to be extracted
        man_kwords = ['EXPTIME', 'LENGTH', 'OWIDTH', 'DRZWIDTH',
                      'XOFFS', 'NAXIS1', 'NAXIS2']

        # the list of optional keywords to be extracted
        opt_kwords = ['SLITWIDT', 'SKY_CPS']

        # open the object image and go to the header
        fits_img = fits.open(self.ext_names['FLT'], mode='readonly')
        fits_head = fits_img[0].header

        # go over all mandatory keywords
        for a_kword in man_kwords:
            # check whether the exposure time is available
            if a_kword in fits_head:
                # store the keyvalue
                self.info[a_kword] = fits_img[0].header[a_kword]
            else:
                # error and out
                err_msg = (f"The keyword: {a_kword} is missing in the image header: {self.ext_names['FLT']}")
                raise Exception(err_msg)

        for a_kword in opt_kwords:
            # check whether the exposure time is available
            if a_kword in fits_head:
                # store the keyvalue
                self.info[a_kword] = fits_img[0].header[a_kword]
            else:
                # store a default
                self.info[a_kword] = None

        # close the image
        fits_img.close()

    def _create_weight_image(self):
        """Generate a weight image."""

        # Set WHT image to 0.0 at masked FLT pixels, and 1.0 elsewhere
        flt_file = fits.open(self.ext_names['FLT'], mode='readonly')
        ind0 = flt_file[0].data < -900000.0
        ind1 = flt_file[0].data >= -900000.0
        flt_file[0].data[ind0] = 0.0
        flt_file[0].data[ind1] = 1.0
        flt_file.writeto(self.ext_names['WHT'])
        flt_file.close()

        # in the FLT image, replace -infinity with 0.0
        flt_file = fits.open(self.ext_names['FLT'], 'update')
        flt_file[0].data[flt_file[0].data < -900000.0] = 0.0
        flt_file.close()

    def make_sortIndex(self, sortList):
        """Generate the sort index of the object"""
        for index in range(len(sortList)):

            # check whether the contributor comes from the current
            # grism image
            sIndex = self.file_root.find(sortList[index])

            # store the index and
            # exit if yes
            if sIndex > -1:
                self.sortIndex = index
                break

    def check_files(self):
        """Check for all files."""

        # list of keys to check all the time
        checklist = ['FLT', 'ERR', 'CON']

        # keys to check in optimal extraction
        optlist = ['MOD', 'VAR']

        # go over all keys
        for one_check in checklist:
            # if the file in the dictionary does NOT exists
            if not os.path.isfile(self.ext_names[one_check]):
                # complain and out
                err_msg = (f"The file: {self.ext_names[one_check]} does not exist!")
                raise aXeError(err_msg)

        if self.opt_extr:
            # go over all keys
            for one_check in optlist:
                # if the file in the dictionary does NOT exists
                if not os.path.isfile(self.ext_names[one_check]):
                    # complain and out
                    err_msg = (f"The file: {self.ext_names[one_check]} does not exist!")
                    #raise aXeError(err_msg) <--TODO ERRORS WITHOUT

    def isempty(self):
        """Checks whether the files contain meaningful data"""
        isempty = 0

        # open the image
        image = fits.open(self.ext_names['FLT'], mode='readonly')

        # go to data extension
        data_ext = image[0].data

        # check whether average is ZERO or -1.0E+06 and std is ZERO
        if ((data_ext.shape == (10, 10)) and (data_ext.std() == 0.0)):
            if ((data_ext.mean() == 0.0) or (data_ext.mean() == -1.0E+06)):
                # set to empty
                isempty = 1
        if data_ext.shape[1] < 2:
            isempty = 1

        # close the image
        image.close()

        # return result
        return isempty

    def delete_files(self):
        """Delete all files"""
        # go over all drizzle filenames
        for one_file in self.ext_names.values():
            if os.path.isfile(one_file):
                os.unlink(one_file)

    def prepare_drizzle(self):
        """Prepare the drizzling."""

        self._get_header_info()

        # create the weight image
        self._create_weight_image()

        # make the coefficients file
        dcf = DrizzleCoefficients(self.ext_names['FLT'])
        dcf.writeto(self.ext_names['CFF'])

    def regroup(self, objID_dir):
        """Move the files to a new location.

        Parameters
        ----------
        onjID_dir: str

        Returns
        -------
        Nothing

        """

        for key,val in self.ext_names.items():
            # compose the destination name
            new_name = os.path.join(objID_dir, os.path.basename(val))

            # move all existing files
            if os.path.isfile(val):
                shutil.move(val, objID_dir)

            # store the new path
            self.ext_names[key] = new_name

    def get_wht_info(self):
        """Evaluate the weight image."""

        # if the wht-image does NOT exist,
        # store and return None's
        if not os.path.isfile(self.ext_names['WHT']):
            self.npix = None
            self.nwht = None
        else:
            wht_data = fits.getdata(self.ext_names['WHT'])

            # get the number of pixels and the number
            # of good pixels
            npix = wht_data.shape[0] * wht_data.shape[1]
            nwht = int(wht_data.mean() * float(npix))

            # store the number of pixels
            # and the number of pixels with weight
            self.npix = npix
            self.nwht = nwht


class DrizzleCoefficients:
    """Class for a contributing image to a drizzle object"""
    def __init__(self, image):
        # save the image name
        self.image = image

        # extract the coefficients
        self.xcoeffs, self.ycoeffs = self._get_coefficients(image)

        # determine the order of the coefficients
        self.order = self._get_order(self.xcoeffs)

        # generate the header
        self.header = self._make_header(self.image)

    def _get_coefficients(self, image):
        """Extracts the drizzle coefficients.

        Parameters
        ----------
        image: str
            Name of the image

        Returns
        -------
        xcoeffs: list
            drizzle x-coefficients from the header
        ycoeffs: list
            drizzle y-coefficients from the header
        """

        xcoeffs = []
        ycoeffs = []

        im_head = fits.getheader(image)

        # search at most 10 coefficients
        for index in range(10):

            # form the x- and y- keyword
            drz0_keyword = 'DRZ0{0:01d}'.format(index)
            drz1_keyword = 'DRZ1{0:01d}'.format(index)

            # stop if one of the keywords is missing
            if not (drz0_keyword in im_head and drz1_keyword in im_head):
                break

            # append the keywords to the list
            xcoeffs.append(str(im_head[drz0_keyword]))
            ycoeffs.append(str(im_head[drz1_keyword]))

        # return the coefficients
        return xcoeffs, ycoeffs

    def _get_order(self, coeff_list):
        """Determine the order of the polynomial."""

        # dictionary with the fixed names for the orders
        fixed_orders = {1: 'constant', 2: 'linear', 3: 'quadratic', 4: 'cubic'}

        # try to determine the order
        order = (-1.0 + math.sqrt(1.0 + 8.0*len(coeff_list))) / 2.0

        # check that the computed order is integer
        if math.fabs(int(order) - order) > 0.0:
            # give message and out
            err_msg = (f"The number of coefficients in image: {self.image} is wrong: {order}!")
            raise aXeError(err_msg)

        # check whether the order is 'known'
        if order not in fixed_orders:
            err_msg = (f"The order of the coefficients in image: {self.image} is not allowed: {order}")
            raise aXeError(err_msg)

        # return the order
        return fixed_orders[order]

    def _make_header(self, image):
        """Generate a header"""
        # make an empty header
        header = []

        # make some specific header phrases
        header.append('-----------------------------------------')
        header.append('coeficients file generated for aXedrizzle')
        header.append(f'from keywords in image: {os.path.basename(image)}')

        return header

    def writeto(self, file_name):
        """Write coefficients to a file."""

        # delete any previous file
        if os.path.isfile(file_name):
            os.unlink(file_name)

        # open the file
        coeff_file = open(file_name, 'w+')

        # append the header lines
        for a_line in self.header:
            coeff_file.write(f"# {a_line}\n")

        # write the order to the file
        coeff_file.write(f"{self.order}\n")

        # write the x- and y-coefficients to the file
        coeff_file.write(f"{' '.join(self.xcoeffs)}\n")
        coeff_file.write(f"{' '.join(self.ycoeffs)}\n")

        # close the file
        coeff_file.close()
