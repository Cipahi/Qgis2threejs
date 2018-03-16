# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Qgis2threejs
                                 A QGIS plugin
 export terrain data, map canvas image and vector data to web browser
                              -------------------
        begin                : 2014-01-16
        copyright            : (C) 2014 Minoru Akagi
        email                : akaginch@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import os

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QColor, QImage, QPainter
from qgis.core import QgsMapLayer

from . import qgis2threejstools as tools
from .qgis2threejstools import logMessage


class DataManager:
  """ manages a list of unique items """

  def __init__(self):
    self._list = []

  def count(self):
    return len(self._list)

  def _index(self, data):
    if data in self._list:
      return self._list.index(data)

    index = len(self._list)
    self._list.append(data)
    return index


class ImageManager(DataManager):

  IMAGE_FILE = 1
  CANVAS_IMAGE = 2
  MAP_IMAGE = 3
  LAYER_IMAGE = 4

  def __init__(self, exportSettings):
    DataManager.__init__(self)
    self.exportSettings = exportSettings
    self._renderer = None

  def imageIndex(self, path):
    img = (self.IMAGE_FILE, path)
    return self._index(img)

  def canvasImageIndex(self, transp_background):
    img = (self.CANVAS_IMAGE, transp_background)
    return self._index(img)

  def mapImageIndex(self, width, height, extent, transp_background):
    img = (self.MAP_IMAGE, (width, height, extent, transp_background))
    return self._index(img)

  def layerImageIndex(self, layerids, width, height, extent, transp_background):
    img = (self.LAYER_IMAGE, (layerids, width, height, extent, transp_background))
    return self._index(img)

  def mapCanvasImage(self, transp_background=False):
    """ returns base64 encoded map canvas image """
    canvas = self.exportSettings.canvas
    size = self.exportSettings.mapSettings.outputSize()
    if canvas is None or transp_background or True:   #
      return self.renderedImage(size.width(), size.height(), self.exportSettings.baseExtent, transp_background)

    # bad - incompletely rendered image is given
    image = QImage(size.width(), size.height(), QImage.Format_ARGB32_Premultiplied)
    painter = QPainter()
    painter.begin(image)
    canvas.render(painter)
    painter.end()
    return image

  def renderedImage(self, width, height, extent, transp_background=False, layerids=None):
    # render layers with QgsMapRendererCustomPainterJob
    from qgis.core import QgsMapRendererCustomPainterJob
    antialias = True
    settings = self.exportSettings.mapSettings

    # store old map settings
    old_outputSize = settings.outputSize()
    old_extent = settings.extent()
    old_rotation = settings.rotation()
    old_layerids = settings.layerIds()
    old_backgroundColor = settings.backgroundColor()

    # map settings
    settings.setOutputSize(QSize(width, height))
    settings.setExtent(extent.unrotatedRect())
    settings.setRotation(extent.rotation())

    if layerids:
      settings.setLayers(tools.getLayersByLayerIds(layerids))

    if transp_background:
      settings.setBackgroundColor(QColor(Qt.transparent))
    #else:    #TODO: remove
      #settings.setBackgroundColor(self.exportSettings.canvas.canvasColor())

    has_pluginlayer = False
    for layer in settings.layers():
      if layer and layer.type() == QgsMapLayer.PluginLayer:
        has_pluginlayer = True
        break

    # create an image
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    painter = QPainter()
    painter.begin(image)
    if antialias:
      painter.setRenderHint(QPainter.Antialiasing)

    # rendering
    job = QgsMapRendererCustomPainterJob(settings, painter)
    if has_pluginlayer:
      job.renderSynchronously()   # use this method so that TileLayerPlugin layer is rendered correctly
    else:
      job.start()
      job.waitForFinished()
    painter.end()

    # restore map settings
    settings.setOutputSize(old_outputSize)
    settings.setExtent(old_extent)
    settings.setRotation(old_rotation)
    settings.setLayers(tools.getLayersByLayerIds(old_layerids))
    settings.setBackgroundColor(old_backgroundColor)

    return image

    #if exportSettings.localBrowsingMode:
    #else:
    #  texfilename = os.path.splitext(htmlfilename)[0] + "_%d.png" % plane_index
    #  image.save(texfilename)
    #  texSrc = os.path.split(texfilename)[1]
    #  tex["src"] = texSrc

  def image(self, index):
    image = self._list[index]
    imageType = image[0]
    if imageType == self.IMAGE_FILE:
      image_path = image[1]
      if os.path.isfile(image_path):
        return QImage(image_path)
      else:
        logMessage("Image file not found: {0}".format(image_path))
        image = QImage(1, 1, QImage.Format_RGB32)
        image.fill(Qt.lightGray)
        return image

    if imageType == self.MAP_IMAGE:
      width, height, extent, transp_background = image[1]
      return self.renderedImage(width, height, extent, transp_background)

    if imageType == self.LAYER_IMAGE:
      layerids, width, height, extent, transp_background = image[1]
      return self.renderedImage(width, height, extent, transp_background, layerids)

    #imageType == self.CANVAS_IMAGE:
    transp_background = image[1]
    return self.mapCanvasImage(transp_background)

  def base64image(self, index):
    image = self.image(index)
    if image:
      return tools.base64image(image)
    return None

  def write(self, index, path):
    self.image(index).save(path)

  def writeAll(self, pathRoot):
    for i in range(self.count()):
      self.image(i).save("{}_IMG{}.png".format(pathRoot, i))


class MaterialManager(DataManager):

  MESH_LAMBERT = 0
  MESH_PHONG = 1
  LINE_BASIC = 2
  SPRITE = 3

  WIREFRAME = 10
  MESH_LAMBERT_SMOOTH = 0
  MESH_LAMBERT_FLAT = 11

  CANVAS_IMAGE = 20
  MAP_IMAGE = 21
  LAYER_IMAGE = 22
  IMAGE_FILE = 23

  ERROR_COLOR = "0"

  def __init__(self):
    DataManager.__init__(self)
    self.writtenCount = 0

  def _indexCol(self, type, color, opacity=1, doubleSide=False):
    if color[0:2] != "0x":
      color = self.ERROR_COLOR
    mat = (type, color, opacity, doubleSide)
    return self._index(mat)

  def getMeshLambertIndex(self, color, opacity=1, doubleSide=False):
    return self._indexCol(self.MESH_LAMBERT, color, opacity, doubleSide)

  def getSmoothMeshLambertIndex(self, color, opacity=1, doubleSide=False):
    return self._indexCol(self.MESH_LAMBERT_SMOOTH, color, opacity, doubleSide)

  def getFlatMeshLambertIndex(self, color, opacity=1, doubleSide=False):
    return self._indexCol(self.MESH_LAMBERT_FLAT, color, opacity, doubleSide)

  def getLineBasicIndex(self, color, opacity=1):
    return self._indexCol(self.LINE_BASIC, color, opacity)

  def getWireframeIndex(self, color, opacity=1):
    return self._indexCol(self.WIREFRAME, color, opacity)

  def getCanvasImageIndex(self, opacity=1, transp_background=False):
    mat = (self.CANVAS_IMAGE, transp_background, opacity, True)
    return self._index(mat)

  def getMapImageIndex(self, width, height, extent, opacity=1, transp_background=False):
    mat = (self.MAP_IMAGE, (width, height, extent, transp_background), opacity, True)
    return self._index(mat)

  def getLayerImageIndex(self, layerids, width, height, extent, opacity=1, transp_background=False):
    mat = (self.LAYER_IMAGE, (layerids, width, height, extent, transp_background), opacity, True)
    return self._index(mat)

  def getImageFileIndex(self, path, opacity=1, transp_background=False, doubleSide=False):
    mat = (self.IMAGE_FILE, (path, transp_background), opacity, doubleSide)
    return self._index(mat)

  def getSpriteIndex(self, path, opacity=1):
    transp_background = True
    mat = (self.SPRITE, (path, transp_background), opacity, False)
    return self._index(mat)

  def build(self, index, imageManager, filepath=None, url=None):
    mat = self._list[index]
    mt = {
      self.WIREFRAME: self.MESH_LAMBERT,
      self.MESH_LAMBERT_FLAT: self.MESH_LAMBERT,
      self.CANVAS_IMAGE: self.MESH_PHONG,
      self.MAP_IMAGE: self.MESH_PHONG,
      self.LAYER_IMAGE: self.MESH_PHONG,
      self.IMAGE_FILE: self.MESH_PHONG
      }.get(mat[0], mat[0])

    m = {"type": mt}
    transp_background = False
    if mat[0] in [self.CANVAS_IMAGE, self.MAP_IMAGE, self.LAYER_IMAGE, self.IMAGE_FILE, self.SPRITE]:
      if mat[0] == self.CANVAS_IMAGE:
        transp_background = mat[1]
        imgIndex = imageManager.canvasImageIndex(transp_background)
      elif mat[0] == self.MAP_IMAGE:
        width, height, extent, transp_background = mat[1]
        imgIndex = imageManager.mapImageIndex(width, height, extent, transp_background)
      elif mat[0] == self.LAYER_IMAGE:
        layerids, width, height, extent, transp_background = mat[1]
        imgIndex = imageManager.layerImageIndex(layerids, width, height, extent, transp_background)
      elif mat[0] in [self.IMAGE_FILE, self.SPRITE]:
        imagepath, transp_background = mat[1]
        imgIndex = imageManager.imageIndex(imagepath)

      if filepath is None:
        m["image"] = {"object": imageManager.image(imgIndex)}
        #m["image"] = {"base64": imageManager.base64image(imgIndex)}
      else:
        m["image"] = {"url": url}
        # write image to a file
        imageManager.write(imgIndex, filepath)
    else:
      m["c"] = int(mat[1], 16)    # color

    if transp_background:
      m["t"] = 1

    if mat[0] == self.WIREFRAME:
      m["w"] = 1

    if mat[0] == self.MESH_LAMBERT_FLAT:
      m["flat"] = 1

    opacity = mat[2]
    if opacity < 1:
      m["o"] = opacity

    # double sides
    if mat[3]:
      m["ds"] = 1

    return m

  def buildAll(self, imageManager, pathRoot=None, urlRoot=None):
    mList = []
    for i in range(len(self._list)):
      filepath = "{0}_IMG{1}.png".format(pathRoot, i)
      url = "{0}_IMG{1}.png".format(urlRoot, i)
      mList.append(self.build(i, imageManager, filepath, url))
    return mList

  def write(self, f, imageManager):
    if len(self._list) <= self.writtenCount:
      return

    toMaterialType = {self.WIREFRAME: self.MESH_LAMBERT,
                      self.MESH_LAMBERT_FLAT: self.MESH_LAMBERT,
                      self.CANVAS_IMAGE: self.MESH_PHONG,
                      self.MAP_IMAGE: self.MESH_PHONG,
                      self.LAYER_IMAGE: self.MESH_PHONG,
                      self.IMAGE_FILE: self.MESH_PHONG}

    for mat in self._list[self.writtenCount:]:
      m = {"type": toMaterialType.get(mat[0], mat[0])}

      transp_background = False

      if mat[0] == self.CANVAS_IMAGE:
        transp_background = mat[1]
        m["i"] = imageManager.canvasImageIndex(transp_background)
      elif mat[0] == self.MAP_IMAGE:
        width, height, extent, transp_background = mat[1]
        m["i"] = imageManager.mapImageIndex(width, height, extent, transp_background)
      elif mat[0] == self.LAYER_IMAGE:
        layerids, width, height, extent, transp_background = mat[1]
        m["i"] = imageManager.layerImageIndex(layerids, width, height, extent, transp_background)
      elif mat[0] in [self.IMAGE_FILE, self.SPRITE]:
        filepath, transp_background = mat[1]
        m["i"] = imageManager.imageIndex(filepath)
      else:
        m["c"] = mat[1]

      if transp_background:
        m["t"] = 1

      if mat[0] == self.WIREFRAME:
        m["w"] = 1

      if mat[0] == self.MESH_LAMBERT_FLAT:
        m["flat"] = 1

      opacity = mat[2]
      if opacity < 1:
        m["o"] = opacity

      # double sides
      if mat[3]:
        m["ds"] = 1

      index = self.writtenCount
      f.write("lyr.m[{0}] = {1};\n".format(index, tools.pyobj2js(m, quoteHex=False)))
      self.writtenCount += 1


class ModelManager(DataManager):

  def __init__(self):
    DataManager.__init__(self)
    self._collada = False

  def modelIndex(self, path, model_type="JSON"):
    if model_type == "COLLADA":
      self._collada = True

    model = (model_type, path)
    return self._index(model)

  def filesToCopy(self):
    f = []
    if self._collada:
      f.append({"files": ["js/threejs/loaders/ColladaLoader.js"], "dest": "threejs/loaders"})
    return f

  def scripts(self):
    s = []
    if self._collada:
      s.append("threejs/loaders/ColladaLoader.js")
    return s

  def write(self, f):
    if len(self._list) == 0:
      return

    f.write('\n// 3D model data\n')
    for index, model in enumerate(self._list):
      model_type, path = model
      exists = os.path.exists(path)
      if exists and os.path.isfile(path):
        with open(path) as model_file:
          data = model_file.read().replace("\\", "\\\\").replace("'", "\\'").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
        f.write("project.models[%d] = {type:'%s',data:'%s'};\n" % (index, model_type, data))
      else:
        f.write("project.models[%d] = {type:'%s',data:null};\n" % (index, model_type))

        if exists:
          err_msg = "Not 3D model file path"
        else:
          err_msg = "3D model file not found"
        logMessage("{0}: {1} ({2})".format(err_msg, path, model_type))
